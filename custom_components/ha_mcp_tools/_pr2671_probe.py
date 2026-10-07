"""Temporary runner-only review probes; never shipped upstream."""
import asyncio
import importlib.metadata as metadata
import threading
import time
import traceback
from pathlib import Path

from . import card_definitions as cd, custom_cards as cc
from . import card_runtime as runtime

_resolutions = []
_original_resource_path = cc.resource_path
_original_index = cd.CardDefinitions._index
_original_build = cd._build
_saved_definitions = None
_loop_thread = None
_original_provider = runtime._provider_state
_original_dom_url = cc.LINKEDOM_URL
_original_integrity = cc.LINKEDOM_INTEGRITY
_saved_skip_pip = None
_pip_calls = []
_original_requirements = None
_bundle_errors = []
_prepare_bundle = cc._Bundle._prepare
def _record_prepare(self, *args):
    try:
        return _prepare_bundle(self, *args)
    except Exception as exc:
        _bundle_errors.append(traceback.format_exc())
        raise
cc._Bundle._prepare = _record_prepare
cc._RUNTIME_JS = cc._RUNTIME_JS.replace("if (op === 'check') {", """
    if (op === 'target_check') {
      var checked = [];
      [{source: 'editor', element: slot2.editor}, {source: 'card', element: new C()}].forEach(function(t) {
        try { t.element.hass=__hass; t.element.setConfig(p.config); checked.push({source:t.source,error:null}); }
        catch(e) { checked.push({source:t.source,error:__verdict(e)}); }
      });
      return {value:checked};
    }
    if (op === 'check') {""")

def _conflict():
    raise ValueError("injected conflicting installed provider")

def _resource_path(*args):
    _resolutions.append(threading.get_ident() == _loop_thread)
    return _original_resource_path(*args)

async def _prep(hass, msg):
    global _saved_definitions, _saved_skip_pip, _original_requirements
    operation = msg.get("operation", "state")
    if operation == "skip_pip":
        import homeassistant.requirements as requirements
        if _saved_skip_pip is None:
            _saved_skip_pip = hass.config.skip_pip
            _original_requirements = requirements.async_process_requirements
            async def recorded(*args, **kwargs):
                _pip_calls.append(list(args[2]))
                return await _original_requirements(*args, **kwargs)
            requirements.async_process_requirements = recorded
        hass.config.skip_pip = True
    elif operation == "restore_pip":
        import homeassistant.requirements as requirements
        if _saved_skip_pip is not None:
            hass.config.skip_pip = _saved_skip_pip
            requirements.async_process_requirements = _original_requirements
            _saved_skip_pip = None
            if cd._definitions is None:
                cd._build_task = None
    elif operation == "empty_index":
        _saved_definitions = cd._definitions
        cd._definitions = None
        cd._build_task = None
        cd.CardDefinitions._index = lambda self: None
    elif operation == "restore":
        cd.CardDefinitions._index = _original_index
        cd._definitions = _saved_definitions
        cd._build_task = None
        runtime._provider_state = _original_provider
        cc.LINKEDOM_URL = _original_dom_url
        cc.LINKEDOM_INTEGRITY = _original_integrity
        cc._dom_failed_at = None
    elif operation == "quickjs_conflict":
        _saved_definitions = cd._definitions
        cd._definitions = None
        cd._build_task = None
        runtime._provider_state = _conflict
    elif operation == "offline":
        cc.LINKEDOM_URL = "http://127.0.0.1:9/unavailable"
    elif operation == "bad_integrity":
        cc.LINKEDOM_INTEGRITY = "sha512-invalid"
    elif operation == "transient_runtime":
        saved = (cd._definitions, cd._build_task, cd.async_ensure_runtime)
        failed_at = getattr(cd, "_build_failed_at", None)
        calls = 0
        async def transient(hass):
            nonlocal calls
            calls += 1
            return False if calls == 1 else await saved[2](hass)
        try:
            cd._definitions = cd._build_task = None
            cd.async_ensure_runtime = transient
            first = await cd.async_get_definitions(hass)
            second = await cd.async_get_definitions(hass)
            suppressed = calls == 1
            if hasattr(cd, "_build_failed_at"):
                cd._build_failed_at = time.monotonic() - 601
            recovered = await cd.async_get_definitions(hass)
            return {"result": {"first_unavailable": first is None, "cooldown_suppressed": suppressed, "second_unavailable": second is None, "recovered": recovered is not None, "calls": calls, "fault": "injected one failed runtime initialization result; actual provider unchanged"}}
        finally:
            cd._definitions, cd._build_task, cd.async_ensure_runtime = saved
            if hasattr(cd, "_build_failed_at"):
                cd._build_failed_at = failed_at
    elif operation == "source_growth":
        custom = await cc.async_get_custom_cards(hass, timeout=5)
        def examine():
            path = Path(hass.config.path(".storage", "ha_mcp_tools", "pr2671-growth-probe.js"))
            limit = cc._MAX_SOURCE_BYTES
            bundle = None
            try:
                path.write_text(";", encoding="utf-8")
                old_size = path.stat().st_size
                path.write_text(";" + " " * 512, encoding="utf-8")
                cc._MAX_SOURCE_BYTES = 64
                cards = cc.CustomCards(custom._dom)
                bundle = cards._load(path, old_size)
                return {"stale_stat_size": old_size, "actual_bytes": path.stat().st_size, "probe_limit": 64, "accepted": bundle is not None, "reason": cards._skipped.get(path)}
            finally:
                if bundle is not None:
                    bundle.close()
                cc._MAX_SOURCE_BYTES = limit
                path.unlink(missing_ok=True)
        return {"result": await hass.async_add_executor_job(examine)}
    elif operation == "counted_warnings":
        custom = await cc.async_get_custom_cards(hass, timeout=5)
        original_check = custom.check
        checked = []
        def counted(tag, config):
            result = original_check(tag, config)
            checked.append(tag)
            return result
        custom.check = counted
        started = time.monotonic()
        try:
            warnings = await cd.async_card_warnings(hass, msg["config"])
            return {"result": {"warnings": warnings, "seconds": time.monotonic() - started, "checked": len(checked)}}
        finally:
            custom.check = original_check
    elif operation == "warnings":
        started = time.monotonic()
        warnings = await cd.async_card_warnings(hass, msg["config"])
        return {"result": {"warnings": warnings, "seconds": time.monotonic() - started}}
    elif operation == "round6_definitions":
        definitions = await cd.async_get_definitions(hass)
        def inspect():
            descriptions = {tag: definitions.describe(tag) for tag in sorted(definitions._card_types)}
            bodies = {tag: definitions._editor_body(tag) for tag in ("gauge", "alarm-panel")}
            return {"descriptions": descriptions, "editor_bodies": bodies}
        return {"result": await hass.async_add_executor_job(inspect)}
    elif operation == "card_index":
        import re
        definitions = await cd.async_get_definitions(hass)
        def inspect():
            return {tag: bool(re.search(r"setConfig\([\w$]+\)\{", definitions._body(f"hui-{tag}-card"))) for tag in sorted(definitions._card_types)}
        return {"result": await hass.async_add_executor_job(inspect)}
    elif operation == "editor_details":
        import re
        definitions = await cd.async_get_definitions(hass)
        def inspect():
            result = {}
            for tag in ("markdown", "gauge", "clock", "logbook", "entity"):
                editor = definitions._editor(tag)
                body = definitions._body(editor)
                match = cd._STRUCT_RE.search(body)
                expr = cd._local_definition(body, match.group(3)) if match else None
                row = {"editor": editor, "body": body, "card_body": definitions._body(f"hui-{tag}-card"), "struct": expr}
                if expr:
                    try:
                        row["keys"] = definitions._evaluate("struct", body, expr, key=tag)
                    except ValueError as exc:
                        row["error"] = str(exc)
                        failed = re.search(r"module (\d+)", str(exc))
                        if failed:
                            source = definitions._engine("source", {"id": failed.group(1)}).get("value", "")
                            row["failed_source"] = source
                            row["dependencies"] = {alias: definitions._engine("source", {"id": ident}).get("value", "") for alias, ident in cd._ALIAS_RE.findall(source)[:12]}
                result[tag] = row
            return result
        return {"result": await hass.async_add_executor_job(inspect)}
    elif operation == "custom_targets":
        custom = await cc.async_get_custom_cards(hass, timeout=5)
        def inspect():
            tag = msg["config"]["type"].removeprefix("custom:")
            bundle = custom._owner(tag)
            return bundle.engine("target_check", {"tag": tag, "config": msg["config"]})
        return {"result": await hass.async_add_executor_job(inspect)}
    elif operation == "module_source":
        definitions = await cd.async_get_definitions(hass)
        def inspect():
            return {str(ident): definitions._engine("source", {"id": str(ident)}).get("value", "") for ident in msg["config"]["ids"]}
        return {"result": await hass.async_add_executor_job(inspect)}
    elif operation == "cold_cache":
        if cc._refresh_task is not None:
            await asyncio.shield(cc._refresh_task)
        def clear():
            if cc._custom is not None:
                for path in list(cc._custom._bundles):
                    cc._custom._drop(path)
            cache = Path(hass.config.path(".storage", "ha_mcp_tools", f"linkedom-{cc.LINKEDOM_VERSION}.js"))
            cache.unlink(missing_ok=True)
        await hass.async_add_executor_job(clear)
        cc._custom = None
        cc._dom_failed_at = None
        cc._refresh_task = None
    cache = Path(hass.config.path(".storage", "ha_mcp_tools", f"linkedom-{cc.LINKEDOM_VERSION}.js"))
    def inventory():
        versions = {}
        for name in ("quickjs", "quickjs-ng"):
            try:
                versions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pass
        return versions, cache.is_file()
    versions, cached = await hass.async_add_executor_job(inventory)
    return {"result": {
        "definitions_built": cd._definitions is not None,
        "build_started": cd._build_task is not None,
        "builtin_count": len(cd._definitions._card_types) if cd._definitions else 0,
        "custom_created": cc._custom is not None,
        "loaded_bundles": sum(b is not None for _, b in cc._custom._bundles.values()) if cc._custom else 0,
        "resolve_on_event_loop": list(_resolutions),
        "quickjs_versions": versions,
        "linkedom_cached": cached,
        "skip_pip": hass.config.skip_pip,
        "pip_calls": list(_pip_calls),
        "bundle_errors": list(_bundle_errors),
    }}

def specs(hass, vol):
    global _loop_thread
    _loop_thread = threading.get_ident()
    cc.resource_path = _resource_path
    return [({vol.Required("type"): "pr2671_probe", vol.Optional("operation"): str, vol.Optional("config"): dict}, lambda hass, msg, result: result, _prep)]
