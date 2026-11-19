"""Report real client responses observed in tests without modifying them."""
import os
from pathlib import Path
import sys


def pytest_configure(config):
    monitor = sys.monitoring
    tool = 4
    monitor.use_tool_id(tool, "3023 Core response audit")
    observed = set()

    def observe(code, offset, result):
        if isinstance(result, dict) and result.get("success") is False:
            message = sys._getframe(1).f_locals.get("message", {})
            observed.add((message.get("type", "?"), str(result.get("error_code"))))

    def enter(code, offset):
        if code.co_name == "send_websocket_message" and code.co_filename.endswith("/ha_mcp/client/rest_client.py"):
            monitor.set_local_events(tool, code, monitor.events.PY_RETURN)
        return monitor.DISABLE

    monitor.register_callback(tool, monitor.events.PY_START, enter)
    monitor.register_callback(tool, monitor.events.PY_RETURN, observe)
    monitor.set_events(tool, monitor.events.PY_START)
    config.issue3023_codes = observed


def pytest_unconfigure(config):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    Path(f"/tmp/issue3023-{worker}.tsv").write_text("".join(
        f"{op}\t{code}\n" for op, code in sorted(config.issue3023_codes)
    ))
    sys.monitoring.set_events(4, 0)
    sys.monitoring.free_tool_id(4)
