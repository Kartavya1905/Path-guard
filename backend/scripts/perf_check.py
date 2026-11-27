"""Responsiveness check: representative 5- and 10-task workflows, plus large fixtures.

Run with the API up (docker compose up -d), from backend/:
    .venv/Scripts/python.exe scripts/perf_check.py [--api http://127.0.0.1:8600]

For each workflow it creates a project through the API, generates the map,
accepts every suggestion with its branch, gives every modeled link an
explicit hypothetical estimate (complete decisions summing to 100%), then
times analysis, worst-case ranking and the report. Projects are deleted after.
It also times the engine on synthetic branching maps (pathguard_engine.fixtures).
Prints a Markdown table.
"""

import argparse
import json
import statistics
import time
import urllib.request
from decimal import Decimal

from pathguard_engine import analyze, explore
from pathguard_engine.fixtures import branching_map

TASKS = ["Confirm requirements", "Agree API contract", "Build integration", "Legal sign-off",
         "Write test plan", "QA in staging", "Security review", "Train support team",
         "Staged rollout", "Monitor conversion"]
OWNERS = ["Ana", "Ben", "Ana", "Chen", "Ben", "Dee", "Chen", "Eve", "Ana", "Dee"]


def call(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req) as res:
        payload = res.read()
    return (json.loads(payload) if payload else None), (time.perf_counter() - t) * 1000


def estimates(graph: dict) -> dict:
    """Give every modeled link an explicit hypothetical estimate; decisions total 100%."""
    live = [t for t in graph["transitions"] if t["review_status"] in ("accepted", "edited")]
    by_source: dict[str, list] = {}
    for t in live:
        by_source.setdefault(t["source_node_id"], []).append(t)
    for links in by_source.values():
        if len(links) == 1:
            shares = [Decimal(1)]
        else:
            others = Decimal("0.1") / (len(links) - 1)
            shares = [Decimal("0.9") if t["outcome"] == "success" else others for t in links]
            if not any(t["outcome"] == "success" for t in links):
                shares = [Decimal(1) / len(links)] * len(links)
            shares = [x.quantize(Decimal("1e-10")) for x in shares]
            shares[-1] = Decimal(1) - sum(shares[:-1])  # exact 100% after rounding
        for t, p in zip(links, shares, strict=True):
            t["probability"] = {"kind": "point", "value": str(p),
                                "source": {"type": "hypothetical", "assumption": "perf check"}}
    for g in graph["decision_groups"]:
        g["complete"] = True
    return graph


def workflow(base: str, n: int) -> dict:
    tasks = [{"id": f"tsk_perf{n:02d}{i:04d}", "title": TASKS[i], "position": i,
              "owner": OWNERS[i], "duration_days": "3"} for i in range(n)]
    prereqs = [{"id": f"pre_perf{n:02d}{i:04d}", "before_task_id": tasks[i]["id"],
                "after_task_id": tasks[i + 1]["id"]} for i in range(n - 1)]
    draft, _ = call(base, "POST", "/api/projects", {
        "title": f"Perf {n} tasks", "start": "Change approved", "goal": "Launched",
        "context": {"domain": "checkout", "stakeholders": ["A", "B", "C"]},
        "tasks": tasks, "prerequisites": prereqs})
    pid = draft["plan"]["project"]["id"]
    try:
        out, t_gen = call(base, "POST", f"/api/projects/{pid}/graph/generate", {"revision": 1})
        rev = out["revision"]
        for r in out["graph"]["risks"]:
            res, _ = call(base, "POST", f"/api/projects/{pid}/risks/{r['id']}/review",
                          {"decision": "accept", "model_branch": True, "revision": rev})
            rev = res["revision"]
        graph = estimates(res["graph"])
        saved, t_save = call(base, "PUT", f"/api/projects/{pid}/graph",
                             {"graph": graph, "revision": rev})
        timings = {"analysis": [], "explorer": [], "report": []}
        for _ in range(5):
            a, t = call(base, "GET", f"/api/projects/{pid}/analysis")
            timings["analysis"].append(t)
            _, t = call(base, "GET", f"/api/projects/{pid}/explorer")
            timings["explorer"].append(t)
            _, t = call(base, "GET", f"/api/projects/{pid}/report")
            timings["report"].append(t)
        g = saved["graph"]
        return {"name": f"{n} tasks (API)", "nodes": len(g["nodes"]),
                "links": len(g["transitions"]), "status": a["status"],
                "states": a["limits"]["expansions_used"],
                "generate_ms": t_gen, "save_ms": t_save,
                **{f"{k}_ms": statistics.median(v) for k, v in timings.items()}}
    finally:
        call(base, "DELETE", f"/api/projects/{pid}")


def fixture(steps: int) -> dict:
    g = branching_map(steps, retries=2)
    t = time.perf_counter()
    a = analyze(g)
    t_an = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    explore(g)
    t_ex = (time.perf_counter() - t) * 1000
    return {"name": f"branching fixture, {steps} steps (engine)", "nodes": len(g.nodes),
            "links": len(g.transitions), "status": a.status,
            "states": a.limits.expansions_used,
            "paths": f"top {a.limits.paths_shown}"
                     + (" (more not listed)" if a.limits.path_search_truncated else ""),
            "analysis_ms": t_an, "explorer_ms": t_ex}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8600")
    args = ap.parse_args()
    rows = [workflow(args.api, 5), workflow(args.api, 10), fixture(10), fixture(40), fixture(250)]
    print("| Case | Nodes | Links | Result | States | Generate | Save | Analysis | "
          "Worst cases | Report | Paths |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        def ms(k: str, r: dict = r) -> str:
            return f"{r[k]:.0f} ms" if k in r else "—"
        print(f"| {r['name']} | {r['nodes']} | {r['links']} | {r['status']} | {r['states']} | "
              f"{ms('generate_ms')} | {ms('save_ms')} | {ms('analysis_ms')} | "
              f"{ms('explorer_ms')} | {ms('report_ms')} | {r.get('paths', '—')} |")


if __name__ == "__main__":
    main()
