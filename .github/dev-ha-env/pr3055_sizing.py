import asyncio
import base64
import hashlib
import io
import logging
import sys
from pathlib import Path
import httpx
from PIL import Image
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
sys.path.insert(0, "tests")
from test_constants import TEST_TOKEN
logging.disable(logging.CRITICAL)

async def main():
    base = "http://127.0.0.1:8123"
    endpoint = base + Path("../mcp-path").read_text().strip()
    async with httpx.AsyncClient(base_url=base, headers={"Authorization":"Bearer " + TEST_TOKEN}, timeout=60) as ha:
        print("Core", (await ha.get("/api/config")).json()["version"], flush=True)
        cams = []
        for name in ["pr3055-sizing.jpg", "pr3055-large.png"]:
            start = await ha.post("/api/config/config_entries/flow", json={"handler":"local_file","show_advanced_options":False})
            start.raise_for_status()
            finish = await ha.post("/api/config/config_entries/flow/"+start.json()["flow_id"], json={"file_path":"/config/www/"+name})
            print("local_file",name,finish.json()["type"], flush=True)
            assert finish.json()["type"] == "create_entry"
            cams.append("camera."+name.split(".")[0].replace("-","_"))
        for _ in range(50):
            states = (await ha.get("/api/states")).json()
            if set(cams).issubset({s["entity_id"] for s in states}): break
            await asyncio.sleep(1)
        else: raise AssertionError([s["entity_id"] for s in states if s["entity_id"].startswith("camera.")])
        cases = [{},{"width":160},{"height":120},{"width":160,"height":120}]
        async with streamable_http_client(endpoint) as (read,write,_), ClientSession(read,write) as session:
            await session.initialize()
            for eid in ["image.qr_code", *cams]:
                dom = eid.split(".")[0]
                for dims in (cases if eid!=cams[1] else [{}]):
                    r = await ha.get("/api/"+dom+"_proxy/"+eid,params=dims)
                    r.raise_for_status()
                    raw = r.content
                    size = Image.open(io.BytesIO(raw)).size
                    print("Core",eid,dims,r.status_code,size,len(raw),hashlib.sha256(raw).hexdigest()[:12],flush=True)
                    result = await session.call_tool("ha_get_camera_image",{"entity_id":eid,**dims})
                    if result.isError:
                        print("MCP",eid,dims,"error",result.content[0].text[:450],flush=True)
                    else:
                        data = next(x for x in result.content if x.type=="image")
                        content=base64.b64decode(data.data)
                        print("MCP",eid,dims,result.content[0].text,len(content),hashlib.sha256(content).hexdigest()[:12],flush=True)
asyncio.run(main())
