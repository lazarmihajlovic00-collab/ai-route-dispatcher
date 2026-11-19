"""Command line: seed, plan, ask, eval, serve."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import service
from .domain import parse_hhmm
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="dispatcher", description="AI route planner and dispatcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="generate a delivery day and plan it")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--seconds", type=float, default=10.0)
    a = sub.add_parser("ask", help="give the dispatcher a command in Serbian")
    a.add_argument("text")
    a.add_argument("--clock", default="10:00")
    a.add_argument("--rules", action="store_true", help="use the rule parser even if an API key is set")
    a.add_argument("--user", default="dispecer")
    ap_ = sub.add_parser("apply", help="approve a proposal")
    ap_.add_argument("id", type=int)
    ap_.add_argument("--user", required=True)
    sub.add_parser("show", help="print the active plan")
    e = sub.add_parser("eval", help="run the evaluation")
    e.add_argument("--seeds", default="1,2,3,4,5,6,7,8")
    e.add_argument("--seconds", type=float, default=10.0)
    e.add_argument("--llm", action="store_true")
    e.add_argument("--out", default=str(ROOT / "docs" / "eval_report.md"))
    v = sub.add_parser("serve", help="start the API and dashboard")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.cmd == "seed":
        out = service.seed(Store(), args.seed, args.seconds)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif args.cmd == "ask":
        from .agent import handle_command

        r = handle_command(Store(), args.text, parse_hhmm(args.clock), args.user, use_llm=False if args.rules else None)
        print(r.answer)
        if r.cost_usd:
            print(f"\n[{r.source}, {r.cost_usd:.4f} USD, {r.latency_ms} ms]")
    elif args.cmd == "apply":
        print(service.apply_proposal(Store(), args.id, args.user))
    elif args.cmd == "show":
        from .agent import handle_with_rules

        print(handle_with_rules(Store(), "pregled plana", 0, "cli").answer)
    elif args.cmd == "eval":
        from .eval.run import run_all

        out = Path(args.out)
        rep = run_all([int(x) for x in args.seeds.split(",")], args.seconds, args.llm, out, out.with_suffix(".json"))
        print(out.read_text(encoding="utf-8").split("## 1.")[1][:1500])
        print(f"\nReport: {out}")
        _ = rep
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("dispatcher.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
