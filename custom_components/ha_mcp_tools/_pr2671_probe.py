"""Temporary runner-only review probes; never shipped upstream."""
import asyncio
import importlib.metadata as metadata
import threading
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
        _bundle_errors.append(str(exc))
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
    elif operation == "warnings":
        return {"result": {"warnings": await cd.async_card_warnings(hass, msg["config"])}}
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
