"""Headers for authenticated worker-to-API calls."""

from noteflow_worker.config import settings
from noteflow_worker.runtime.execution_context import current_workspace_id


def internal_api_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = settings.noteflow_internal_token.strip()
    if not token:
        # Local profile remains loopback-only and does not require service auth.
        return headers
    workspace_id = current_workspace_id()
    if not workspace_id:
        raise RuntimeError("Internal API call has no task workspace context")
    headers["X-NoteFlow-Internal-Token"] = token
    headers["X-NoteFlow-Workspace-Id"] = workspace_id
    return headers
