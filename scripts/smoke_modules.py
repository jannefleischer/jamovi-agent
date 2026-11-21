"""Smoke test for the preinstalled library modules (MEDA, snowCluster, semlj,
GAMLj3): opens an example data set per module like the browser does and runs
one analysis through the agent tool API.

Runs inside the jamovi image next to smoke.py (same arguments):

    python3 smoke_modules.py <base url> [--token T]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import aiohttp

from smoke import FakeBrowser, check

M = "/usr/lib/jamovi/modules"
HOLZINGER = ("visual =~ x1 + x2 + x3\n"
             "textual =~ x4 + x5 + x6\n"
             "speed =~ x7 + x8 + x9")

# (data file, ns, name, options, text expected in the results)
CASES = [
    (f"{M}/snowCluster/data/iris.omv", "snowCluster", "kmeans",
     {"vars": ["Sepal.Length", "Sepal.Width", "Petal.Length", "Petal.Width"], "k": 3},
     "luster"),
    (f"{M}/MEDA/data/music.omv", "MEDA", "MCA", None, "Dim"),
    (f"{M}/semlj/data/holzinger_long.csv", "semlj", "semljsyn",
     {"code": HOLZINGER, "vars": [f"x{i}" for i in range(1, 10)]}, "visual"),
    (f"{M}/GAMLj3/data/clustermanymodels.csv", "GAMLj3", "gamljmixed",
     {"dep": "ycont", "covs": ["x"], "model_terms": [["x"]],
      "cluster": ["cluster"], "re": [[["Intercept", "cluster"]]]}, "Fixed Effects"),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--token")
    args = ap.parse_args()
    headers = {"Authorization": f"token {args.token}"} if args.token else {}
    base = args.base.rstrip("/")
    ok = True
    async with aiohttp.ClientSession(headers=headers) as http:
        for path, ns, name, options, expect in CASES:
            browser = FakeBrowser(http, base)
            await browser.open(path)
            await asyncio.sleep(1.0)
            if options is None:  # MCA: all factor columns of the data set
                async with http.post(f"{base}/agent/tools/get_dataset_schema", json={}) as r:
                    schema = await r.json()
                cols = [c["name"] for c in schema.get("columns", schema.get("variables", []))
                        if c.get("measure_type", c.get("measureType")) in ("nominal", "ordinal")]
                options = {"actvars": cols[:5]}
            async with http.post(f"{base}/agent/tools/create_analysis",
                                 json={"ns": ns, "name": name, "options": options}) as r:
                res = await r.json()
            text = json.dumps(res, ensure_ascii=False)
            print(f"   {ns}::{name}:", text[:400])
            ok &= check(r.status == 200 and "error" not in res and expect in text,
                        f"{ns}::{name} returns results")
            await browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
