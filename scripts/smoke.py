"""End-to-end smoke test: acts like the browser, then drives the agent API.

Runs with jamovi's own Python inside the jamovi image (needs aiohttp and
jamovi.server.jamovi_pb2):

    python3 smoke.py <base url> [--token T] [--chat "message"]

<base url> is the jamovi root, e.g. http://127.0.0.1:8888/user/test/jamovi
(through jupyter-server-proxy). Exit code 0 = all checks passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

import aiohttp
from jamovi.server import jamovi_pb2 as jcoms

DATA = "/usr/lib/jamovi/modules/jmv/data/ToothGrowth.csv"


class FakeBrowser:
    """Holds the single client connection of an instance, like the browser."""

    def __init__(self, http: aiohttp.ClientSession, base: str):
        self.http = http
        self.base = base
        self.instance_id = ""
        self.analyses: dict[int, jcoms.AnalysisResponse] = {}
        self._ws = None
        self._reader = None

    async def open(self, path: str) -> None:
        form = aiohttp.FormData()
        form.add_field("options", json.dumps({"path": path}))
        async with self.http.post(f"{self.base}/open", data=form) as r:
            text = await r.text()
        ids = re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27}", text)
        if not ids:
            raise RuntimeError(f"open failed: {text[:300]}")
        self.instance_id = ids[-1]

        ws_url = f"{self.base}/{self.instance_id}/coms".replace("http", "ws", 1)
        self._ws = await self.http.ws_connect(ws_url)
        msg = jcoms.ComsMessage(id=1, instanceId=self.instance_id,
                                payload=jcoms.InstanceRequest().SerializeToString(),
                                payloadType="InstanceRequest")
        await self._ws.send_bytes(msg.SerializeToString())
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for m in self._ws:
            if m.type != aiohttp.WSMsgType.BINARY:
                continue
            coms = jcoms.ComsMessage()
            coms.ParseFromString(m.data)
            if coms.payloadType == "AnalysisResponse":
                resp = jcoms.AnalysisResponse()
                resp.ParseFromString(coms.payload)
                if resp.name != "empty":
                    self.analyses[resp.analysisId] = resp

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


def check(ok: bool, what: str) -> bool:
    print(("PASS " if ok else "FAIL ") + what)
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--token", default="")
    ap.add_argument("--chat", default="")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    headers = {"Authorization": f"token {args.token}"} if args.token else {}
    ok = True

    async with aiohttp.ClientSession(headers=headers) as http:
        async with http.get(f"{base}/agent/health") as r:
            ok &= check(r.status == 200, f"agent/health -> {r.status}")

        browser = FakeBrowser(http, base)
        await browser.open(DATA)
        await asyncio.sleep(1.0)
        print("instance", browser.instance_id)

        async with http.get(f"{base}/agent/instances") as r:
            insts = await r.json()
        ok &= check(any(i["instance_id"] == browser.instance_id and i["active"] for i in insts),
                    "instance listed as active")

        # Phase A: server-side analysis reaches the browser connection
        async with http.post(f"{base}/agent/tools/create_analysis",
                             json={"ns": "jmv", "name": "descriptives",
                                   "options": {"vars": ["len"]}}) as r:
            res = await r.json()
        print(json.dumps(res, ensure_ascii=False)[:600])
        aid = res.get("analysis_id", 0)
        ok &= check(aid % 2 == 1, f"server-side analysis has odd id ({aid})")
        await asyncio.sleep(1.0)
        got = browser.analyses.get(aid)
        ok &= check(got is not None, "browser connection received the analysis")
        ok &= check(got is not None and got.status == jcoms.AnalysisStatus.Value("ANALYSIS_COMPLETE"),
                    "browser got complete results")
        ok &= check("len" in json.dumps(res), "tool returned results as text")

        for tool, body in (("get_dataset_schema", {}),
                           ("describe_variables", {"variables": ["len", "supp"]}),
                           ("search_analyses", {"query": "t-test"}),
                           ("get_analysis_schema", {"ns": "jmv", "name": "ttestIS"})):
            async with http.post(f"{base}/agent/tools/{tool}", json=body) as r:
                res = await r.json()
            ok &= check(r.status == 200 and "error" not in res, f"tool {tool}")
            print("   ", json.dumps(res, ensure_ascii=False)[:300])

        if args.chat:
            async with http.post(f"{base}/agent/chat", json={"message": args.chat}) as r:
                async for line in r.content:
                    line = line.decode().strip()
                    if line.startswith("data:"):
                        print("   ", line[:400])
            ok &= check(len(browser.analyses) >= 1, "chat run finished")

        await browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
