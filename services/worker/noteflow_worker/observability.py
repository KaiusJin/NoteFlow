from __future__ import annotations

import logging
from contextlib import contextmanager

from noteflow_worker.config import settings

logger = logging.getLogger(__name__)


def initialize_observability() -> None:
    if settings.worker_metrics_port > 0:
        from prometheus_client import start_http_server

        start_http_server(settings.worker_metrics_port, addr=settings.worker_metrics_bind_address)
    if not settings.otel_exporter_otlp_endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "noteflow-worker"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument()


@contextmanager
def task_span(task_type: str, task_id: str, event_id: str | None):
    from opentelemetry import trace

    tracer = trace.get_tracer("noteflow.worker")
    with tracer.start_as_current_span(
        "noteflow.task",
        attributes={
            "noteflow.task.type": task_type,
            "noteflow.task.id": task_id,
            "noteflow.event.id": event_id or "",
        },
    ):
        yield
