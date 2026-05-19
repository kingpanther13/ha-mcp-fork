# Design: Close out #1349 (HAOS E2E tier v2)

**Status:** Draft — pending approval
**Issue:** [#1349](https://github.com/homeassistant-ai/ha-mcp/issues/1349)
**Predecessor PR:** #1361 (item 7 — inaddon CI tier scaffolding + items 5, 4)
**Target PR scope:** Items 1, 2, 6, 8, 9 — plus diagnosing the still-firing `test_backup.py` external skips.

## Goals

1. **Eliminate every `pytest.skip()` call** in test bodies on both HAOS tiers, with one exception: the existing `tests/src/e2e/workflows/system/test_supervisor_mock.py` module (the mock-fixture test surface stays as `external_only`-marked tests for testcontainer + external-HAOS coverage; we add a parallel inaddon-targeted module that covers the same Supervisor wire contract against the real Supervisor).
2. **Add HAOS-only coverage** that the testcontainer tier physically can't reach: addon lifecycle ops against real Supervisor, integration setup flows for companion integrations that live inside their addons (ESPHome, Node-RED), real Sun-position math (vs the testcontainer stub), Local Calendar lifecycle.
3. **Keep marker-based tier skips** (`haos_only`, `inaddon_only`, etc.) — those are structural tier filtering, not "test broken" skips. The user explicitly OK'd these when legitimate.

## Non-goals (explicitly dropped from original #1349)

- **Item 3 — mock RTSP/MQTT feeders for Z2M + Frigate.** Out of scope per user. Z2M/Frigate stay `start=False` in `ADDONS`. Their lifecycle tests (commit 3) test what's reachable without `start` — `ha_get_addon` info, options-get, logs-fetch, plus assertion that `start` returns a recognizable error. **No `pytest.skip()` calls** — the tests run and assert the addon's stopped-state behavior.

## Architecture

### New infrastructure pieces

1. **SSH-addon docker-exec helper** (used by commits 6 + 8).
   Location: `tests/src/haos_runtime.py` — new function `_docker_exec_in_addon(slug, cmd, *, timeout=30)`.
   Mechanism: SSH to `127.0.0.1:22222` (host port forward already set up in `boot_haos_qemu`), credentials `root:haosdebug` already provisioned by `install_advanced_ssh` in `build_image.py`, run `docker exec <container_name> <cmd>`. Returns stdout. Raises on non-zero exit.
   Used by:
   - Commit 6 — assert/manipulate addon role state for 401/403 path tests
   - Commit 8 — `chmod 444 /data/saved_tools.json` mid-test for `save_warning` rollback E2E

2. **Inaddon supervisor-mock parallel module** (commits 5 + 6).
   New file: `tests/src/e2e/workflows/system/test_supervisor_inaddon.py`.
   Marker: `pytestmark = [pytest.mark.inaddon_only]`.
   Mirrors each class from `test_supervisor_mock.py` with assertions adapted for real Supervisor responses (shape-not-content). Existing `test_supervisor_mock.py` stays as the external/container coverage path.

3. **SSH addon as the "target" for destructive tests** (commit 6).
   The SSH addon (`local_homeassistant_advanced_ssh`) is already installed and running on the qcow2 from `install_advanced_ssh`. We use it as the target for `TestSettingsUiRestart` (so the test addon — `ha_mcp_dev` — doesn't get killed mid-session), and for `TestMockResilience` 401/403 paths (its `hassio_role` defaults to lower than `manager`, giving us a real 403 source).

### Components touched

| Component | Change | Why |
|---|---|---|
| `tests/haos_image_build/build_image.py` | `Node-RED` + `ESPHome Device Builder` flip from default-`start=False` to explicit `start=True`. Mosquitto stays `start=False` (per user — not needed). | Lifecycle tests in commit 2 need them running. |
| `tests/src/haos_runtime.py` | `_docker_exec_in_addon` helper. | Commits 6 + 8. |
| `tests/src/e2e/haos_only/test_addon_lifecycle.py` (new) | Node-RED + ESPHome + Frigate + Z2M lifecycle tests. | Items 1 + part of 3 (Frigate/Z2M without feeders). |
| `tests/src/e2e/haos_only/test_integration_setup.py` (new) | ESPHome companion, Node-RED companion, Local Calendar, real Sun position. | Item 2. |
| `tests/src/e2e/workflows/system/test_supervisor_inaddon.py` (new) | Real-Supervisor parallel of supervisor_mock test classes. | Item 6. |
| `tests/src/e2e/tools/test_create_custom_tool.py` | Delete Monty placeholder skip (line 1677); replace filesystem-poisoning placeholder (line 1861) with real test. | Items 9 + 8. |
| `tests/initial_test_state/` or `build_image.py` | Diagnose + fix backup-password skip. May involve verifying `.storage/backup` shape, bake order, or HA Core's backup-config load path on HAOS. | Open question — see commit 9. |

### Commit sequence

Independent — each commit is shippable on its own and CI passes after each.

**Commit 1 — Bake: flip Node-RED + ESPHome to `start=True`**
- 1-line per addon in `ADDONS` tuple
- Cache-miss bake adds ~30-60s; cache-hit free
- Validates: cached qcow2 ships with both addons running

**Commit 2 — Item 1: Node-RED + ESPHome lifecycle tests**
- New `tests/src/e2e/haos_only/test_addon_lifecycle.py`
- Per addon (× 2): `ha_manage_addon` action=stop, =start, =restart; options-get returns schema-valid dict; options-set + options-get round-trip; logs-fetch returns non-empty string with timestamps.
- ~8 tests total
- Marker: `pytest.mark.haos_only`. No `pytest.skip()`.

**Commit 3 — Item 1 (limited): Frigate + Z2M reachable-without-running tests**
- Same file
- Per addon (× 2): `ha_get_addon(slug=...)` returns info dict, addon `state` is "stopped" or "boot_fail", options-get returns schema-valid dict, logs-fetch returns either the boot-fail log or empty string (both are valid stopped-addon shapes), `ha_manage_addon` action=start returns a structured error containing recognizable text (e.g., "missing config" or "device not found").
- ~4 tests
- Tests as `xfail` for the `start` call only if it errors with the expected reason — never `skip`.

**Commit 4 — Item 2: integration setup tests**
- New `tests/src/e2e/haos_only/test_integration_setup.py`
- ESPHome companion integration: query `ha_get_integration(domain="esphome")`, assert `state` is loaded; the ESPHome addon's installation auto-registers the integration (this is what HAOS specifically provides).
- Node-RED companion integration: same shape via `node_red` domain.
- Local Calendar: `ha_config_set_helper(domain="local_calendar", ...)`, verify calendar entity appears via `ha_get_entity`, add an event via `ha_config_set_calendar_event`, retrieve via `ha_config_get_calendar_events`.
- Sun: `ha_get_state("sun.sun")` returns realistic next_dawn/next_dusk timestamps (not stubbed values).
- ~6 tests. `pytest.mark.haos_only`. No skips.

**Commit 5 — Item 6: supervisor_mock migration, easy classes**
- New `tests/src/e2e/workflows/system/test_supervisor_inaddon.py`
- `pytestmark = [pytest.mark.inaddon_only]`
- Migrate:
  - `TestGetLogsSystemService` → call `ha_get_logs(source="system_service", slug=<svc>)` for each of audio/cli/core/dns/host/multicast/observer/supervisor. Assert response is `success=True`, `log` is non-empty string, `total_lines >= 1`. (8 parametrized tests.)
  - `TestGetLogsSystemService::test_unknown_service_rejected_by_caller_validation` — caller-side validation; real-Supervisor behavior identical to mock. (1 test.)
  - `TestBugReportAddonLogs::test_fetches_self_logs` → `_fetch_addon_logs()` directly hits the addon's own `/addons/self/logs`. Assert non-empty, contains `INFO`/`DEBUG`/timestamps. (1 test.)
  - `TestBugReportAddonLogs::test_returns_empty_when_token_missing` → temporarily `monkeypatch.delenv("SUPERVISOR_TOKEN")` inside the addon's env via `_docker_exec_in_addon` — actually no, this is in-process logic; the function reads its OWN env. The TEST PROCESS's env is what matters. Since we're running inaddon (the addon IS the server), this test needs special handling: either skip (would violate user rule) or call into the addon via an MCP tool that triggers `_fetch_addon_logs()` and verify the empty-string return. **Open question — addressed in commit 5 implementation.**
  - `TestFixtureWiring::test_is_running_in_addon_returns_true` — assert `is_running_in_addon()` is True (no mock needed; the addon process actually has `SUPERVISOR_TOKEN` set). (1 test.)
  - `TestFixtureWiring::test_base_url_resolves_to_mock` → rename to `test_base_url_resolves_to_supervisor`; assert `get_supervisor_base_url()` returns `"http://supervisor"`. (1 test.)
  - `TestFixtureWiring::test_default_when_override_unset` — same in inaddon mode (the addon's env has no override). (1 test.)
- ~12 tests total. All `inaddon_only`. No `pytest.skip()`.

**Commit 6 — Item 6: supervisor_mock migration, hard classes via SSH addon retarget**
- Same file
- `TestSettingsUiRestart::test_restart_request_succeeds` → retarget at `/addons/local_homeassistant_advanced_ssh/restart` instead of `/addons/self/restart`. The SSH addon being restarted is harmless; our dev addon (the MCP server we're connected to) keeps running. Wait for SSH-port reachability post-restart as the assertion. (1 test.)
- `TestSettingsUiRestart::test_restart_request_rejects_bad_token` → same retarget, with bogus auth header. Assert 401. (1 test.)
- `TestMockResilience::test_unauthorized_supervisor_call_surfaces_as_tool_error` → real 401 path. Use `monkeypatch.setenv("SUPERVISOR_TOKEN", "bad-on-purpose")` inside the test process (the MCP client). Since the addon-side server has its OWN SUPERVISOR_TOKEN, this only affects code that reads test-process env (which is the test harness's HA client, not the addon). **Open question** — the original mock test specifically tests the addon's behavior with a bad token. To exercise this on inaddon, we need to ask the addon to make a Supervisor call with a forced-bad token. Two options:
  - (a) Add a test-only env var the addon recognizes to force-override its SUPERVISOR_TOKEN (rejected — production-code mutation for tests is bad).
  - (b) Make this an in-process unit-test surface and accept that the 401-path inaddon coverage stays in the existing mock-tier external_only tests. (My recommendation — the wire-contract IS tested by the mock; the inaddon version validates the happy path, mock-tier validates the error paths.)
- `TestMockResilience::test_insufficient_role_supervisor_call_surfaces_403` → retarget the call at SSH addon (lower `hassio_role` → 403 for protected endpoints). (1 test.)
- `TestMockResilience::test_concurrent_log_fetches` + `test_addon_logs_limit_truncation` → exercise real concurrent Supervisor calls. (2 tests.)
- ~5-6 tests. **One open question** on the bad-token path (see (a) vs (b) above).

**Commit 7 — Item 9: delete Monty placeholder**
- `tests/src/e2e/tools/test_create_custom_tool.py:1677` — delete the `pytest.skip(...)` and its surrounding test body if the test is purely the skip; otherwise delete the entire test function. Add a one-line module-level comment pointing at `test_saved_tools_persistence.py` for the unit-tested classifier coverage.
- Net effect: one skipped test removed; no coverage loss (unit tests cover it).

**Commit 8 — Item 8: filesystem-poisoning E2E via SSH-addon docker exec**
- Add `_docker_exec_in_addon(slug, cmd, *, timeout=30)` to `tests/src/haos_runtime.py`.
- Replace the `pytest.skip(...)` at line 1861 with real test:
  1. Determine the addon container name via `ha_get_addon(slug="local_ha_mcp_dev")` → `container` field.
  2. `_docker_exec_in_addon(slug, ["chmod", "444", "/data/saved_tools.json"])` — make the file unwriteable.
  3. Call `ha_manage_custom_tool(action="save", ...)` via MCP.
  4. Assert response contains `save_warning` populated and `saved_as=None`.
  5. `_docker_exec_in_addon(slug, ["chmod", "644", "/data/saved_tools.json"])` — restore (in `finally`).
- Test marker: `pytest.mark.inaddon_only` (because it requires the dockerized addon).
- ~110 LOC: ~50 helper + ~60 test.

**Commit 9 — Fix `test_backup.py` skips on external HAOS**
- Currently shows 3 skips on external CI even after the `.storage/backup` seed.
- Investigate:
  - Confirm the file is in the cached qcow2 (`guestfish --ro ... ll /supervisor/homeassistant/.storage/backup`).
  - Confirm HA Core picks it up: check the bake-time HA Core logs for `Loaded ... backup` or similar.
  - If file is present but HA Core doesn't apply the password: check if storage minor_version 1.7 still matches HA 2026.5.x, or if a migration is needed.
  - Fallback: replace the static seed with a runtime `monkeypatch.setenv("HASSIO_BACKUP_PASSWORD", ...)` / WS `backup/config/update` call in the testcontainer + HAOS fixture setup, so the password is applied at session start rather than at bake.
- Net: 3 skips → 0 skips on external (and inaddon, which inherits).

## Data flow

### SSH-exec helper (commits 6 + 8)

```
test process
    │
    ├──> ssh -p 22222 -o ... root@127.0.0.1
    │       │
    │       └──> haosctl docker exec <container> <cmd>
    │               │
    │               └──> <cmd output captured, returned via SSH stdout>
    │
    └──> return stdout to test
```

Failure modes:
- SSH connection refused → SSH addon not running → fail with addon-state diagnostic.
- `docker exec` non-zero exit → command failed inside container → fail with stdout+stderr.
- Timeout → SSH or docker exec hung → fail with timeout.

### Restart retargeting (commit 6)

```
test process (inaddon MCP client)
    │
    ├──> POST /addons/local_homeassistant_advanced_ssh/restart  (via real Supervisor)
    │       │
    │       └──> SSH addon container stops + starts
    │
    └──> wait for SSH port 22222 reachable (proves restart completed)
        │
        └──> assert: dev addon (the MCP server) still reachable on 19583 (unaffected)
```

### Integration setup flow (commit 4)

```
ESPHome companion integration test:
    │
    ├──> assert ha_get_integration("esphome").state == "loaded"
    │       (the ESPHome addon's install auto-registers this integration on first boot)
    │
    └──> assert at least one entity in domain "esphome.*" via ha_search_entities
        (proves the companion integration actually runs, not just listed)
```

## Error handling

| Failure mode | Where | Handling |
|---|---|---|
| SSH connection fails | commits 6, 8 | `_docker_exec_in_addon` raises `RuntimeError` with `Did install_advanced_ssh complete? Check /tmp/haos-e2e-serial.log for SSH addon boot.` |
| Docker exec returns non-zero | commits 6, 8 | Raise with stdout+stderr+exit code |
| SSH addon restart hangs (commit 6) | commit 6 | `wait_for_addon_mcp_ready`-style poll with 60s timeout, then dump SSH-addon logs via supervisor proxy |
| Backup-password storage doesn't apply (commit 9) | bake or test fixture | Diagnose first; either fix the storage seed or switch to WS API setup |
| `ha_manage_addon start` succeeds when expected to fail (commit 3 Frigate/Z2M) | test assertion | Test fails loudly — would mean either the addon CAN start without feeders (unlikely but possible) or our expected-error text is wrong |

## Testing

Each commit's acceptance criterion is the same: green on both `HAOS E2E Tests` and `HAOS E2E Tests (inaddon)` workflows, with the skip count reduced according to the plan above. Final state:

- Inaddon CI: ~838 + 12 (commit 5) + 5 (commit 6) + 0 (commit 7 deletes a skip) + 1 (commit 8) ≈ 856 passed. Skipped: 14 (supervisor_mock external_only, the user-accepted exception).
- External CI: ~856 + 8 (commit 2) + 4 (commit 3) + 6 (commit 4) + 3 (commit 9 unskips) ≈ 877 passed. Skipped: 1 (test_inaddon_source_refresh inaddon_only — structural).

## Open questions

1. **Commit 5 — `test_returns_empty_when_token_missing`.** The mock test verifies behavior when `SUPERVISOR_TOKEN` is unset in the same process as `_fetch_addon_logs()`. On inaddon, the function runs INSIDE the addon container with its own env; we can't easily simulate a missing token without restarting the addon. Resolution to default: keep this one in the mock-tier suite (external_only); inaddon version covers the happy path. Mark in the spec, decide during implementation.

2. **Commit 6 — bad-token 401 path.** Same family of problem. Default: keep error paths in mock-tier; inaddon covers happy path. Document the explicit per-test "lives in mock-tier" status in the new module's docstring.

3. **Commit 9 — backup storage seed effectiveness.** Need to verify on next CI run whether the `.storage/backup` file is making it into the qcow2 and being applied by HA Core. If it is and tests still skip, the skip condition itself may need adjustment (maybe the error message no longer contains "password"?). Investigate during implementation.

## Risks

- **Frigate/Z2M tests in commit 3 may flake** if Supervisor's `state` field for stopped-without-config addons is sometimes `"unknown"` instead of `"stopped"` or `"boot_fail"`. Mitigation: assert membership in a set, not equality.
- **SSH addon restart in commit 6** may take longer on cache-miss runs (cold Docker layer pull). Mitigation: 60s wait; documented timeout error.
- **`_docker_exec_in_addon` SSH overhead** is ~1s per call. Used in only ~5 tests across commits 6+8, so total test-time impact is ~5s. Acceptable.

## Reverting

Each commit is independent. If a commit causes a regression after merge:
- Commits 1-2 (bake start=True): revert flips back to `start=False`; lifecycle tests then skip (re-introducing skips, so any revert here would be paired with reverting the lifecycle tests too).
- Commits 3-8 are pure test additions/deletions: revert is `git revert`-clean.
- Commit 9 may modify either the seed file or the fixture; revert depends on which path was taken.
