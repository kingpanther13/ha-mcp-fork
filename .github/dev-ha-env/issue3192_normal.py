"""Create, read, run, and edit scripts using only public HA-MCP tools."""
import asyncio
import json
import re
from pathlib import Path

from issue3192_crossover import HELPER, SPEAKERS, TARGET, call, helper, service, stdio


async def main():
    url = json.loads(Path("../targets.json").read_text())["ha"]
    scripts = {
        SPEAKERS: {"alias":"Qualification Packaged Speakers", "sequence":[{"event":"qualification_script_only"}], "mode":"single"},
        TARGET: {"alias":"Set Evening Target", "sequence":[{"action":"input_number.set_value",
                 "target":{"entity_id":HELPER}, "data":{"value":67}}], "mode":"single"},
    }
    async with stdio(url, True) as reader, stdio(url, False) as editor:
        guide = await call(editor, "ha_get_skill_guide")
        ack = re.search(r"^Acknowledgment key: (\S+)", guide["content"])
        params = {"BestPracticeKey":ack[1]} if ack else {}
        for key, definition in scripts.items():
            created = await call(editor, "ha_config_set_script", script_id=key, config=definition, **params)
            assert created.get("success"), created
            result = await call(reader, "ha_config_get_script", script_id="script."+key)
            assert result["config"] == definition, result
            print("EVIDENCE normal create + read exact definition:", key, flush=True)
            await service(editor, "script", key)
            state = await call(reader, "ha_get_state", entity_id="script."+key)
            assert state["data"]["attributes"]["last_triggered"], state
        assert await helper(reader) == 67
        print("EVIDENCE both scripts ran; helper reached 67", flush=True)
        # Use the previous getter's optimistic-locking token for the edit.
        updated = scripts[TARGET]
        updated["sequence"][0]["data"]["value"] = 42
        result = await call(editor, "ha_config_set_script", script_id=TARGET, config=updated,
                            config_hash=result["config_hash"], **params)
        assert result.get("success"), result
        result = await call(reader, "ha_config_get_script", script_id="script."+TARGET)
        assert result["config"] == updated, result
        await service(editor, "script", TARGET)
        assert await helper(reader) == 42
        print("EVIDENCE edited script through tool, re-read definition, ran and verified 42", flush=True)

asyncio.run(main())
