"""Concurrent latency benchmark for the NoteFlow API. Stdlib only.

Usage:
    python3 tests/benchmarks/benchmark_api_latency.py [--label NAME] [--base-url URL]
        [--concurrency N] [--requests N] [--output FILE]

Measures p50/p95/p99 wall-clock latency per endpoint under N concurrent
threads. Endpoints: /health (framework overhead), GET /tasks (DB read),
POST /retrieval (fan-out pipeline), POST /search (lexical search).
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Scenario:
    name: str
    method: str
    path: str
    body: dict | None = None


@dataclass
class Result:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


SCENARIOS = [
    Scenario("health", "GET", "/health"),
    Scenario("tasks_list", "GET", "/tasks"),
    Scenario("retrieval", "POST", "/retrieval", {"query": "probability distribution", "mode": "MIXED", "topK": 8}),
]


def fire(base_url: str, scenario: Scenario, result: Result, count: int) -> None:
    for _ in range(count):
        data = None
        headers = {}
        if scenario.body is not None:
            data = json.dumps(scenario.body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base_url + scenario.path, data=data, headers=headers, method=scenario.method
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read()
            elapsed = (time.perf_counter() - started) * 1000
            with result.lock:
                result.latencies_ms.append(elapsed)
        except (urllib.error.URLError, OSError):
            with result.lock:
                result.errors += 1


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def run_scenario(base_url: str, scenario: Scenario, concurrency: int, total_requests: int) -> dict:
    result = Result()
    per_thread = max(1, total_requests // concurrency)
    threads = [
        threading.Thread(target=fire, args=(base_url, scenario, result, per_thread))
        for _ in range(concurrency)
    ]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_seconds = time.perf_counter() - started
    latencies = result.latencies_ms
    if not latencies:
        return {"scenario": scenario.name, "completed": 0, "errors": result.errors}
    return {
        "scenario": scenario.name,
        "completed": len(latencies),
        "errors": result.errors,
        "throughput_rps": round(len(latencies) / wall_seconds, 1),
        "p50_ms": round(percentile(latencies, 0.50), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "p99_ms": round(percentile(latencies, 0.99), 1),
        "mean_ms": round(statistics.fmean(latencies), 1),
        "max_ms": round(max(latencies), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--requests", type=int, default=320)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    # Warm up the JIT/pools so the first bucket does not skew percentiles.
    warmup = Result()
    fire(args.base_url, SCENARIOS[0], warmup, 20)
    if warmup.errors:
        raise SystemExit(f"API at {args.base_url} is not responding ({warmup.errors} errors during warmup)")

    report = {"label": args.label, "concurrency": args.concurrency, "requests": args.requests, "results": []}
    for scenario in SCENARIOS:
        summary = run_scenario(args.base_url, scenario, args.concurrency, args.requests)
        report["results"].append(summary)
        print(json.dumps(summary))
    if args.output:
        with open(args.output, "w") as handle:
            json.dump(report, handle, indent=2)
        print(f"saved -> {args.output}")


if __name__ == "__main__":
    main()
