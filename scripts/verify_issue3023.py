"""Measure sensitivity of selected tests, restoring source before every next probe."""
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

MAIN = Path("src/ha_mcp/__main__.py")
BLUEPRINT = Path("src/ha_mcp/tools/blueprint_write.py")
TESTS = "tests/src/unit/"
cases = [
    ("import wording only, Core code intact", BLUEPRINT,
     'save_error = error_text(response, "Failed to save blueprint")',
     'save_error = "File is present" if error_code(response) == "already_exists" else error_text(response, "Failed to save blueprint")',
     "test_ha_manage_blueprints_write.py::test_a_save_race_on_import_is_reported_as_already_exists"),
    ("constructor before setup, isolated wiring test", MAIN,
     "    ensure_host_origin_guard_default_off()\n\n    try:\n        from ha_mcp.server import HomeAssistantSmartMCPServer\n\n        return HomeAssistantSmartMCPServer()",
     "    try:\n        from ha_mcp.server import HomeAssistantSmartMCPServer\n\n        server = HomeAssistantSmartMCPServer()\n        ensure_host_origin_guard_default_off()\n        return server",
     "test_transport_security.py::test_create_server_disables_guard"),
    ("lose requested exit status", MAIN, "os._exit(code)", "os._exit(0)",
     "test_stdio_shutdown_abort.py"),
    ("ignore requested FastMCP log level", MAIN, "fastmcp_logger.setLevel(log_level)", "fastmcp_logger.setLevel(logging.INFO)",
     "test_oidc_startup.py"),
]

for index, (label, path, old, new, target) in enumerate(cases):
    original = path.read_bytes()
    assert original.count(old.encode()) == 1, label
    report = Path(f"/tmp/verification-{index}.xml")
    try:
        path.write_bytes(original.replace(old.encode(), new.encode(), 1))
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", f"--junitxml={report}", TESTS + target]
        result = subprocess.run(command, check=False)
        assert result.returncode in {0, 1} and report.exists(), (label, result.returncode)
        suite = ET.parse(report)
        cases_run = suite.findall(".//testcase")
        assert cases_run and all(c.find("error") is None for c in cases_run), label
        failed = [c.get("name") for c in cases_run if c.find("failure") is not None]
        print("MEASURED", label, "failures=", failed, "of", len(cases_run), flush=True)
    finally:
        path.write_bytes(original)
