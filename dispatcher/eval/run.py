"""Evaluation: optimizer vs baseline, disruption recovery, and command understanding.

    dispatcher eval                       # all three parts, rules parser only
    dispatcher eval --llm                 # also the LLM dispatcher (needs ANTHROPIC_API_KEY, costs money)
"""

from __future__ import annotations

import json
import random
import statistics
import tempfile
import time
from pathlib import Path

from .. import service
from ..agent.parser import parse
from ..baseline import plan_baseline
from ..data import make_problem
from ..domain import Order, hhmm
from ..replan import Change, diff_plans, replan
from ..solver import plan_day
from ..store import Store

ROOT = Path(__file__).resolve().parent.parent.parent
COMMANDS = Path(__file__).with_name("commands.json")


# ------------------------------------------------------------------------------- 1. optimizer benchmark
def optimizer_benchmark(seeds: list[int], time_limit_s: float) -> dict:
    rows = []
    for sd in seeds:
        p = make_problem(sd)
        b = plan_baseline(p)
        o = plan_day(p, time_limit_s)
        rows.append({"seed": sd, "orders": len(p.orders),
                     "baseline": {k: b.kpis[k] for k in ("cost_rsd", "km", "vehicles_used", "late_stops",
                                                          "late_minutes", "unserved", "crew_hours", "co2_kg")},
                     "optimized": {k: o.kpis[k] for k in ("cost_rsd", "km", "vehicles_used", "late_stops",
                                                           "late_minutes", "unserved", "crew_hours", "co2_kg",
                                                           "trips", "solve_s")},
                     "optimized_hard_violations": o.kpis["hard_violations"],
                     "baseline_hard_violations": b.kpis["hard_violations"]})

    def mean(f):
        return round(statistics.mean(f(r) for r in rows), 1)

    saving = [100 * (r["baseline"]["cost_rsd"] - r["optimized"]["cost_rsd"]) / r["baseline"]["cost_rsd"] for r in rows]
    return {
        "instances": len(rows), "time_limit_s": time_limit_s, "rows": rows,
        "summary": {
            "cost_saving_pct_mean": round(statistics.mean(saving), 1),
            "cost_saving_pct_min": round(min(saving), 1), "cost_saving_pct_max": round(max(saving), 1),
            "cost_saving_rsd_per_day_mean": mean(lambda r: r["baseline"]["cost_rsd"] - r["optimized"]["cost_rsd"]),
            "km_saving_mean": mean(lambda r: r["baseline"]["km"] - r["optimized"]["km"]),
            "crew_hours_saving_mean": mean(lambda r: r["baseline"]["crew_hours"] - r["optimized"]["crew_hours"]),
            "late_stops_baseline_mean": mean(lambda r: r["baseline"]["late_stops"]),
            "late_stops_optimized_mean": mean(lambda r: r["optimized"]["late_stops"]),
            "unserved_optimized_total": sum(r["optimized"]["unserved"] for r in rows),
            "plans_without_hard_violations_pct": round(100 * sum(r["optimized_hard_violations"] == 0 for r in rows) / len(rows), 1),
        },
    }


# ------------------------------------------------------------------------------ 2. disruption recovery
def disruption_benchmark(seeds: list[int], time_limit_s: float, per_seed: int = 3) -> dict:
    rows = []
    for sd in seeds:
        p = make_problem(sd)
        plan = plan_day(p, time_limit_s)
        rng = random.Random(1000 + sd)
        for _ in range(per_seed):
            clock = rng.choice([480, 540, 600, 660])
            busy = [r for r in plan.routes if any(s.depart > clock for t in r.trips for s in t.stops)]
            if not busy:
                continue
            r = rng.choice(busy)
            stranded = sum(1 for t in r.trips for s in t.stops if s.depart > clock)
            t0 = time.perf_counter()
            res = replan(p, plan, Change("breakdown", vehicle_id=r.vehicle_id), clock, 5.0)
            wall = time.perf_counter() - t0
            d = diff_plans(p, plan, res.problem, res.plan)
            rows.append({"seed": sd, "kind": "breakdown", "vehicle": r.vehicle_id, "clock": hhmm(clock),
                         "open_orders": stranded, "unserved_after": len(d["newly_unserved"]),
                         "late_after": len(d["newly_late"]),
                         "late_minutes": sum(x["late_min"] for x in d["newly_late"]),
                         "cost_delta_rsd": d["delta"]["cost_rsd"], "replan_s": round(wall, 2),
                         "hard_violations": res.plan.kpis["hard_violations"]})
        # one urgent order per seed from a customer that did not order today
        ordered = {o.customer_id for o in p.orders}
        cid = rng.choice([c for c in sorted(p.customers) if c not in ordered])
        clock = rng.choice([540, 600, 660])
        o = Order("U900", cid, rng.choice([3, 5, 8, 10]), 0, 2, urgent=True,
                  tw_start=clock + 60, tw_end=clock + 240)
        o.deliver_kg = o.deliver_dp * 55
        t0 = time.perf_counter()
        res = replan(p, plan, Change("add_order", order=o), clock, 5.0)
        wall = time.perf_counter() - t0
        d = diff_plans(p, plan, res.problem, res.plan)
        served = res.plan.stop_of("U900") is not None
        rows.append({"seed": sd, "kind": "urgent_order", "clock": hhmm(clock), "open_orders": 1,
                     "unserved_after": 0 if served else 1, "late_after": len(d["newly_late"]),
                     "late_minutes": sum(x["late_min"] for x in d["newly_late"]),
                     "cost_delta_rsd": d["delta"]["cost_rsd"], "replan_s": round(wall, 2),
                     "hard_violations": res.plan.kpis["hard_violations"]})
    br = [r for r in rows if r["kind"] == "breakdown"]
    open_total = sum(r["open_orders"] for r in br)
    return {
        "scenarios": len(rows), "rows": rows,
        "summary": {
            "breakdowns": len(br),
            "open_orders_total": open_total,
            "delivered_same_day_pct": round(100 * (open_total - sum(r["unserved_after"] for r in br)) / open_total, 1)
            if open_total else 100.0,
            "on_time_of_open_pct": round(100 * (open_total - sum(r["unserved_after"] + r["late_after"] for r in br))
                                         / open_total, 1) if open_total else 100.0,
            "urgent_orders_served": sum(1 for r in rows if r["kind"] == "urgent_order" and r["unserved_after"] == 0),
            "urgent_orders": sum(1 for r in rows if r["kind"] == "urgent_order"),
            "replan_s_median": round(statistics.median(r["replan_s"] for r in rows), 2),
            "replan_s_max": round(max(r["replan_s"] for r in rows), 2),
            "plans_without_hard_violations_pct": round(100 * sum(r["hard_violations"] == 0 for r in rows) / len(rows), 1),
        },
    }


# --------------------------------------------------------------------------- 3. command understanding
FIELDS = ("vehicle", "time", "customer", "pallets", "kg", "empties", "tw_start", "tw_end")


def _resolve(problem, plan, intent: dict) -> dict:
    out = dict(intent)
    if intent.get("intent") in ("add_order", "cancel", "set_window"):
        try:
            out["customer"] = service.resolve_customer(problem, plan, str(intent.get("customer")))
        except service.ServiceError:
            return {"intent": "clarify"}
    return out


def _grade(case: dict, pred: dict) -> tuple[bool, list[str]]:
    exp = case["expect"]
    if case.get("accept_clarify") and pred.get("intent") == "clarify":
        return True, []
    errors = []
    if pred.get("intent") != exp["intent"]:
        return False, [f"intent {pred.get('intent')} != {exp['intent']}"]
    for f in FIELDS:
        if f in exp:
            pv, ev = pred.get(f), exp[f]
            if isinstance(ev, float) and pv is not None:
                ok = abs(float(pv) - ev) < 1e-6
            else:
                ok = pv == ev
            if not ok:
                errors.append(f"{f}: {pv} != {ev}")
    return not errors, errors


def command_eval(use_llm: bool) -> dict:
    cases = json.loads(COMMANDS.read_text(encoding="utf-8"))
    tmp = tempfile.mkdtemp()
    st = Store(str(Path(tmp) / "eval.db"))
    service.seed(st, 1, 3.0)
    _, problem, plan = service.current(st)
    results = {"rules": [], "llm": []}
    for case in cases:
        pred = _resolve(problem, plan, parse(case["text"]))
        ok, errs = _grade(case, pred)
        results["rules"].append({"text": case["text"], "ok": ok, "errors": errs, "pred": pred})
    cost = 0.0
    if use_llm:
        from ..agent.runner import run

        for case in cases:
            r = run(st, case["text"], 600, "eval")
            cost += r.cost_usd
            pred = r.intent or {"intent": "unknown"}
            if pred.get("intent") == "unknown" and r.tool_calls and any(c["name"] == "pronadji_kupca" for c in r.tool_calls):
                pred = {"intent": "clarify"}
            pred = _resolve(problem, plan, pred)
            ok, errs = _grade(case, pred)
            results["llm"].append({"text": case["text"], "ok": ok, "errors": errs, "pred": pred,
                                   "answer": r.answer, "cost_usd": round(r.cost_usd, 4), "latency_ms": r.latency_ms})
    summary = {"cases": len(cases),
               "rules_accuracy_pct": round(100 * sum(r["ok"] for r in results["rules"]) / len(cases), 1)}
    if use_llm:
        summary["llm_accuracy_pct"] = round(100 * sum(r["ok"] for r in results["llm"]) / len(cases), 1)
        summary["llm_cost_usd_total"] = round(cost, 3)
        summary["llm_cost_usd_per_command"] = round(cost / len(cases), 4)
        summary["llm_latency_s_median"] = round(statistics.median(r["latency_ms"] for r in results["llm"]) / 1000, 1)
    return {"summary": summary, "results": results}


# ----------------------------------------------------------------------------------------------- report
def run_all(seeds: list[int], time_limit_s: float, use_llm: bool, out_md: Path, out_json: Path) -> dict:
    report = {"optimizer": optimizer_benchmark(seeds, time_limit_s),
              "disruptions": disruption_benchmark(seeds, min(time_limit_s, 10.0)),
              "commands": command_eval(use_llm)}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(to_markdown(report), encoding="utf-8")
    return report


def _n(x) -> str:
    return f"{x:,.0f}".replace(",", ".") if isinstance(x, (int, float)) else str(x)


def to_markdown(rep: dict) -> str:
    o, d, c = rep["optimizer"], rep["disruptions"], rep["commands"]
    s = o["summary"]
    lines = ["# Evaluation report", "",
             "Generated by `dispatcher eval`. All data is synthetic (see `dispatcher/data.py`). The baseline is a",
             "dispatcher-style heuristic (territories + nearest neighbour, everyone leaves at 06:00), not a",
             "measurement of a real company's manual plans.", "",
             f"## 1. Daily plan: OR-Tools vs baseline ({o['instances']} days, {o['time_limit_s']:.0f} s solver limit)", "",
             "| Metric | Result |", "|---|---|",
             f"| Cost saving per day, mean (min to max) | **{s['cost_saving_pct_mean']}%** ({s['cost_saving_pct_min']}% to {s['cost_saving_pct_max']}%) |",
             f"| Cost saving per day, mean | {_n(s['cost_saving_rsd_per_day_mean'])} RSD |",
             f"| Kilometres saved per day, mean | {s['km_saving_mean']} km |",
             f"| Crew hours saved per day, mean | {s['crew_hours_saving_mean']} h |",
             f"| Late stops per day, baseline vs optimized | {s['late_stops_baseline_mean']} vs **{s['late_stops_optimized_mean']}** |",
             f"| Orders left unserved by the optimizer (all days) | {s['unserved_optimized_total']} |",
             f"| Optimized plans with zero hard violations (validator) | {s['plans_without_hard_violations_pct']}% |", "",
             "| Day | Orders | Baseline cost | Optimized cost | Saving | Baseline km | Optimized km | Late (base/opt) | Vehicles (base/opt) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in o["rows"]:
        b, z = r["baseline"], r["optimized"]
        lines.append(f"| {r['seed']} | {r['orders']} | {_n(b['cost_rsd'])} | {_n(z['cost_rsd'])} | "
                     f"{100 * (b['cost_rsd'] - z['cost_rsd']) / b['cost_rsd']:.1f}% | {b['km']:.0f} | {z['km']:.0f} | "
                     f"{b['late_stops']}/{z['late_stops']} | {b['vehicles_used']}/{z['vehicles_used']} |")
    s = d["summary"]
    lines += ["", f"## 2. Disruptions during the day ({d['scenarios']} scenarios)", "",
              "Random vehicle breakdowns between 08:00 and 11:00 and one urgent order per day.", "",
              "| Metric | Result |", "|---|---|",
              f"| Open stops of broken vehicles | {s['open_orders_total']} in {s['breakdowns']} breakdowns |",
              f"| Delivered the same day | **{s['delivered_same_day_pct']}%** |",
              f"| Delivered the same day and within the window | {s['on_time_of_open_pct']}% |",
              f"| Urgent orders placed | {s['urgent_orders_served']} of {s['urgent_orders']} |",
              f"| Re-planning time, median (max) | {s['replan_s_median']} s ({s['replan_s_max']} s) |",
              f"| New plans with zero hard violations | {s['plans_without_hard_violations_pct']}% |", "",
              "| Day | Event | Vehicle | Clock | Open stops | Not today | Late | Cost delta (RSD) | Time (s) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in d["rows"]:
        lines.append(f"| {r['seed']} | {r['kind']} | {r.get('vehicle', '')} | {r['clock']} | {r['open_orders']} | "
                     f"{r['unserved_after']} | {r['late_after']} | {_n(r['cost_delta_rsd'])} | {r['replan_s']} |")
    s = c["summary"]
    lines += ["", f"## 3. Understanding dispatcher commands ({s['cases']} commands in Serbian)", "",
              "Hand-written commands with the expected action and parameters (`dispatcher/eval/commands.json`),",
              "including hard ones: nicknames (\"dvojka\"), units in crates, ambiguous customer names, times like \"11-13\".", "",
              "| Method | Accuracy |", "|---|---|", f"| Rule parser (no AI) | {s['rules_accuracy_pct']}% |"]
    if "llm_accuracy_pct" in s:
        lines += [f"| LLM dispatcher | **{s['llm_accuracy_pct']}%** |", "",
                  f"LLM cost: {s['llm_cost_usd_per_command']} USD per command, median latency {s['llm_latency_s_median']} s."]
    else:
        lines += ["| LLM dispatcher | run `dispatcher eval --llm` with `ANTHROPIC_API_KEY` set |"]
    lines += ["", "Rule parser misses:", ""]
    for r in c["results"]["rules"]:
        if not r["ok"]:
            lines.append(f"- \"{r['text']}\": {'; '.join(r['errors'])}")
    if c["results"]["llm"]:
        lines += ["", "LLM misses:", ""]
        for r in c["results"]["llm"]:
            if not r["ok"]:
                lines.append(f"- \"{r['text']}\": {'; '.join(r['errors'])}")
    return "\n".join(lines) + "\n"
