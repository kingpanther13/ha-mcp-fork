# DELETE BEFORE OPENING PR

Working notes for PR #2 (branch `claude/youthful-pascal-izgkvw-pr2`). Everything
planned or deferred to this PR is listed here. Delete this file in the last
commit before the PR is opened.

## 1. Rebase onto master

- PR #1 (homeassistant-ai/ha-mcp#3081) is merged. This branch was built on an
  earlier head of PR #1's branch, so rebase onto `origin/master` with
  `git rebase --onto origin/master <PR #1's old head>`, keeping only PR #2's own
  commits. PR #2 is no longer stacked: open it against `master`.
- Expect conflicts in the alphabetical exemption lists (`rules/*.yml`,
  `tests/src/unit/ruff.toml`, `pyproject.toml`, `tests/core_stand_in_guard.py`)
  and in any file both PRs touched. Re-run the unlist step after the rebase.

## 2. Pass the checks #3081 added

PR #2's 50 files and every file it changes must pass these, which did not exist
when the PR #2 agents ran:

- `scripts/stand_in_lists_ratchet.py`:
  - A file taken off any list (ast-grep `ignores`, `_GRANDFATHERED`, `SLF001`,
    the mypy list) must be on none.
  - A changed file on a `SLF001` list may not gain private accesses.
- `rules/no-test-registry-store-writes.yml` (no list): no writes into
  `.issues`, `.entities`, `.deleted_entities`, `.devices`, `.deleted_devices`,
  `.areas`, `.floors`, `.labels`, `.categories`.
- `rules/no-test-websocket-replies.yml` (no list): no test-registered WebSocket
  command and no reply written on a connection. Re-registering Core's own
  handler to observe, hold or drop is allowed.

## 3. Items deferred to this PR

### 3a. String-named private patches (deferred from #3081)

Add an ast-grep rule that refuses a string-named patch of a private name, e.g.
`patch("mod._fetch_history", ...)`, `patch.object(obj, "_x")`,
`monkeypatch.setattr(obj, "_x", ...)`, `monkeypatch.setattr("mod._x", ...)` or
`delattr`. Do this wherever SLF001 already refuses private access. Use the
`SLF001` per-file lists as its exemption list, so no new list is added. SLF001
cannot see these because the name is a string, and the private-import check
only sees imports. Some of these patches hide hand-typed Core or Supervisor
replies behind our own private wrappers (e.g. `_call_addon_api`,
`_fetch_history`, `_ws_send`). Others shorten private constants or replace
PyPI or Gemini calls; those become public seams.

- Hide a Core or Supervisor reply → convert onto real Core.
- Replace this repo's own environment read or an upstream call → make the seam
  public.
- Cover the rule in `test_core_reply_rules.py` and describe it in
  `tests/AGENTS.md`. The ast-grep rule count there and in
  `docs/agents/development.md` goes from ten to eleven.

Hits on master after #3081, in files not on a `SLF001` list:

169 hits in 52 files (master after #3081)
- `tests/src/unit/test_backup_dashboards.py` (28): `_component_dashboard_config`, `_dashboards_via_component`, `_resolve_dashboard`, `_ws_send`
- `tests/src/unit/test_tools_history.py` (13): `_fetch_history`, `_fetch_statistics`
- `tests/src/unit/test_dashboard_edit_routing.py` (11): `_ensure_dashboard_exists`
- `tests/src/unit/test_embedded_listen.py` (8): `_READY_POLL_INTERVAL_SECONDS`, `_READY_STALL_TIMEOUT_SECONDS`, `_READY_TOTAL_CAP_SECONDS`, `_async_access_token`, `_async_ensure_package`, `_async_warn_on_dependency_conflicts` …
- `tests/src/unit/test_tools_addons_array_patch.py` (8): `_call_addon_api`
- `tests/src/unit/_main_web.py` (7): `_get_mcp`, `_get_server`, `_http_settings_mounted`, `_http_settings_prefix`, `_run_entrypoint`, `_setup_logging` …
- `tests/src/unit/test_custom_card_lifecycle.py` (6): `_Bundle`, `_TOTAL_MEMORY`, `_async_refresh`, `_refresh_task`
- `tests/src/unit/test_entity_backup_component.py` (6): `_ws_send`
- `tests/addon/webhook_proxy/_start.py` (4): `_supervisor_get`, `_supervisor_get_text`, `_supervisor_post`, `_wait_for_ha_restart`
- `tests/src/unit/test_config_entry_reload_watch.py` (4): `_COMMIT_VISIBLE_TIMEOUT`, `_observe_reload_settled`, `_subscribe_entry_changes`
- `tests/src/unit/test_custom_component_file_edits.py` (4): `_caller_token_ok`, `_current_extra_dirs`, `_is_path_allowed_for_dir`, `_write_denied`
- `tests/src/unit/test_e2e_wait_helpers.py` (4): `_open_ha_ws`, `_ws_wait_for_predicate`
- `tests/src/unit/test_ha_search_dashboard_leg_failure.py` (4): `_search_dashboards_surface`
- `tests/src/unit/test_renamed_tools.py` (4): `_get_config_path`
- `tests/src/unit/_backup_snapshot_fakes.py` (3): `_BACKUP_MAX_WAIT_S`, `_supervisor_api_call`
- `tests/src/unit/test_backup_snapshot.py` (3): `_BACKUP_MAX_WAIT_S`, `_BACKUP_PROGRESS_INTERVAL_S`
- `tests/src/unit/test_component_ws_config_references.py` (3): `_core`
- `tests/src/unit/test_custom_component_volume_writes.py` (3): `_in_container`, `_mount_points`
- `tests/src/unit/test_deep_search_cancellation.py` (3): `_score_flow_entry`, `_search_helper_type`, `_search_one_dashboard`
- `tests/src/unit/test_wait_helpers.py` (3): `_POLLING_BACKSTOP_INTERVAL`, `_get_waiter_ws_client`
- `tests/src/unit/policy/test_mrtr_transport.py` (2): `_wait_for_decision`
- `tests/src/unit/test_component_automation_config.py` (2): `_async_validate_config_item`
- `tests/src/unit/test_component_diagnostics.py` (2): `_async_paired_server_requirement`, `_installed_ha_mcp_version`
- `tests/src/unit/test_config_get_component_routing.py` (2): `_RESOLVE_RETRY_DELAY`
- `tests/src/unit/test_ha_search_response_contract.py` (2): `_prefetch_shared_search_snapshots`
- `tests/src/unit/test_hacs_actions.py` (2): `_hacs_info`, `_hacs_search`
- `tests/src/unit/test_overview_system_info.py` (2): `_http_settings_mounted`, `_http_settings_prefix`
- `tests/src/unit/test_version.py` (2): `_running_in_docker`, `_running_python`
- `tests/addon/webhook_proxy/test_relay_logging.py` (1): `_INBOUND_LOG_CAP`
- `tests/addon/webhook_proxy/test_start_stale_oauth.py` (1): `_probe_oauth_active`
- `tests/src/unit/_backup_restore_helpers.py` (1): `_backup_mgr`
- `tests/src/unit/_embedded_server_helpers.py` (1): `_async_paired_server_requirement`
- `tests/src/unit/_llm_api_helpers.py` (1): `_mcp_session`
- `tests/src/unit/test_addon_connection_hints.py` (1): `_create_ingress_session`
- `tests/src/unit/test_automation_errors.py` (1): `_DESCRIPTIONS_TIMEOUT_S`
- `tests/src/unit/test_backup_settings_coordination.py` (1): `_resolve_default_dir`
- `tests/src/unit/test_component_info_timezone_contract.py` (1): `_config_time_zone`
- `tests/src/unit/test_dashboard_write_not_sent.py` (1): `_check_dashboard_replace_hash`
- `tests/src/unit/test_device_tools_on_core.py` (1): `_async_matching_config_entries_json_fragments`
- `tests/src/unit/test_flow_helper_backup.py` (1): `_fetch_helper_flow_types`
- `tests/src/unit/test_flow_progress.py` (1): `_request`
- `tests/src/unit/test_ha_timezone_component_routing.py` (1): `_config_time_zone`
- `tests/src/unit/test_haos_addon_ha_link_wait.py` (1): `_probe_addon_ha_link`
- `tests/src/unit/test_http_run_kwargs.py` (1): `_cleanup_resources`
- `tests/src/unit/test_radio_update_entity_component_routing.py` (1): `_do_device_get`
- `tests/src/unit/test_remove_helper_by_entity.py` (1): `_fetch_helper_flow_types`
- `tests/src/unit/test_remove_helper_storage_id.py` (1): `_REGISTRY_RETRY_BASE_DELAY`
- `tests/src/unit/test_remove_helpers_wait_warnings.py` (1): `_REGISTRY_RETRY_BASE_DELAY`
- `tests/src/unit/test_rest_client_get_logbook.py` (1): `_request`
- `tests/src/unit/test_search_composed_name_component_failure.py` (1): `_search_entities`
- `tests/src/unit/test_search_composed_name_without_component.py` (1): `_search_entities`
- `tests/src/unit/test_warn_default_path_exposed.py` (1): `_is_running_in_container`

### 3b. Convert `test_radio_zwave_parameters.py` to real `zwave_js`

PR #2's version registers its own `zwave_js/*` WebSocket commands and writes
their replies, which the no-test-websocket-replies rule now refuses. Run Core's
real `zwave_js` integration as `test_device_enrichment.py` does in master:
replace only the Z-Wave JS server connection, as Core's own zwave_js tests do.

### 3c. Fix the three duplicate-code ratchet hits

- `tests/src/unit/_config_writes.py::error_body` copies
  `test_tools_logs_fault_log.py::_parse_tool_error`. Import the shared one.
- `test_bug_report_sources.py::read_only_client` and
  `test_rest_client_get_config_entry.py::read_only_client` are the same. Keep
  one definition and import it.
- `_hacs_marketplace.py::sent_commands` and
  `test_component_device_seam.py::_sent` are the same. Keep one definition and
  import it.

Re-run `uv run python scripts/duplicate_code_ratchet.py --check` after the
rebase: master now has more helpers, e.g. `_client_spies.py`.

### 3d. Raise the B18 xfail with the user

`test_backup_capture_cancellation.py::test_cancelled_capture_waits_for_writer_before_next_safety_capture[True]`
is a strict xfail: "a writer that fails after its capture was cancelled reaches
the event loop as an unhandled shielded-future error". A backup restore is
cancelled while its safety snapshot is being written, then the write fails with
`OSError`. On Python 3.14 the error reaches HA's loop exception handler even
though the code consumes it, which logs one "Error doing job" line; no data is
affected.

- Ask the user whether to fix it.
- If fixing: reproduce it live on the dev environment by following its docs,
  keep the test as the failing regression test, fix it and drop the xfail.
- A strict xfail must not ship as "expected".

### 3e. Dead-code candidates: same treatment as PR #1

Check each against every caller, the client's own error handling and Core's
source. Remove dead code with the tests that only existed for it. Anything that
only serves an older Core or component is labelled a legacy fallback with
versions.

- `policy/value_sources.py`: the dict-shape branches. Core's `/api/services`
  always answers a list.
- `server_channels.approval_ws_client`: the `except AttributeError` fallback.

### 3f. Drop the `sanitize_log_text` private alias

B15 made `sanitize_log_text` public but kept a private alias, because an
unconverted test file still imported the old name. Update that import and
remove the alias; don't ship a shim.

### 3g. Note: untyped dashboard resources

B15 noticed that re-creating a deleted untyped dashboard resource sends no
`res_type`, which Core refuses. Untyped rows only exist in stores imported by
an older Core, so this is a legacy-data case. Check it and say so in the PR,
or ask the user.

## 4. Before opening

- Run every PR #2 file through the checker (pytest, ruff incl. SLF001, mypy,
  every `rules/*.yml`).
- Run full local CI against the merge base: fast checks, mypy
  (`scripts/mypy_tests_exact.py`), ratchets, CodeQL, unit tests.
- Delete `tests/testing_config/` (leaked test output) before committing.
- Delete this file.
- Dry-run merge against every open PR (`git merge-tree --write-tree`) and report
  conflicts.
- Show the user the draft description first. Fill in the PR template headings,
  open as a draft against `master`, and add no Claude Code footer.
- Push only when nothing is uncommitted and no non-E2E CI is running. Push once.
