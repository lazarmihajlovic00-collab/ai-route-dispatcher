"""Explanations in Serbian, built only from the diff and the plan (no model needed).

The LLM dispatcher writes its own wording, but it receives exactly these facts from the tools, and this
template is the fallback when no API key is set.
"""

from __future__ import annotations

from .domain import VEHICLE_LABEL, Problem, hhmm
from .plan import Plan


def rsd(x: float) -> str:
    s = f"{abs(round(x)):,}".replace(",", ".")
    return ("-" if x < 0 else "") + s + " RSD"


def signed(x: float, unit: str = "") -> str:
    v = round(x, 1)
    txt = f"{v:+.1f}".replace(".", ",") if isinstance(v, float) and v != int(v) else f"{int(v):+d}"
    return txt + unit


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def vlabel(problem: Problem, vid: int) -> str:
    v = problem.vehicle(vid)
    return f"Vozilo {vid} ({VEHICLE_LABEL[v.kind]})"


def describe_change(problem: Problem, change: dict) -> str:
    k = change["kind"]
    if k == "breakdown":
        when = f" od {hhmm(change['clock'])}" if change.get("mode") == "intraday" else " (pre polaska)"
        return f"{vlabel(problem, change['vehicle_id'])} je van upotrebe{when}"
    if k == "add_order":
        o = change["order"]
        c = problem.customers[o["customer_id"]]
        w = ""
        if o.get("tw_start") is not None:
            w = f", prozor {hhmm(o['tw_start'])}-{hhmm(o['tw_end'])}"
        pal = f"{o['deliver_dp'] / 10:.1f}".replace(".", ",")
        return f"Hitna porudžbina: {c.name} ({c.town}), {pal} pal., {o['deliver_kg']} kg{w}"
    if k == "cancel":
        o_c = change.get("customer_name", change.get("order_id"))
        return f"Otkazana isporuka: {o_c}"
    if k == "set_window":
        return (f"Novi prozor za {change.get('customer_name', change.get('order_id'))}: "
                f"{hhmm(change['tw_start'])}-{hhmm(change['tw_end'])}")
    return k


def explain(problem: Problem, new_plan: Plan, diff: dict, change: dict) -> str:
    lines = [describe_change(problem, change) + "."]
    mode = change.get("mode")
    if mode == "intraday":
        lines.append("Već isporučeno ostaje kako jeste, a kamioni koji su na putu zadržavaju svoj redosled. "
                     "Otvorene isporuke idu u nove ture iz baze.")
    else:
        lines.append("Ništa još nije utovareno, pa je ceo dan ponovo optimizovan, uz što manje promena.")

    if change["kind"] == "breakdown" and mode == "intraday" and not diff["moved"] and not diff["newly_unserved"]:
        lines.append(f"Vozilo {change['vehicle_id']} je do tada već obavilo sve svoje isporuke, "
                     "pa ništa ne treba preraspodeliti.")
    if diff["moved"]:
        by_to: dict[int, int] = {}
        for m in diff["moved"]:
            by_to[m["to_vehicle"]] = by_to.get(m["to_vehicle"], 0) + 1
        parts = [f"{n} → {vlabel(problem, v)}" for v, n in sorted(by_to.items())]
        n = len(diff['moved'])
        lines.append(f"Premešteno {n} {plural(n, 'isporuka', 'isporuke', 'isporuka')} na druga vozila: " + "; ".join(parts) + ".")
    for t in diff["second_trips"]:
        lines.append(f"{vlabel(problem, t['vehicle'])} kreće u dodatnu turu u {hhmm(t['depart'])} "
                     f"({t['stops']} {plural(t['stops'], 'stanica', 'stanice', 'stanica')}).")
    for x in diff.get("removed", []):
        where = "roba ostaje u vozilu i vraća se u bazu" if mode == "intraday" else "ne utovaruje se"
        lines.append(f"Stanica {x['customer']} uklonjena je iz rute vozila {x['vehicle']} ({where}).")
    target = None
    if change["kind"] == "add_order":
        target = change["order"]["id"]
    elif change["kind"] == "set_window":
        target = change["order_id"]
    if target:
        hit = new_plan.stop_of(target)
        if hit:
            r, _, s = hit
            c = problem.customers[s.customer_id]
            late = f", {s.late_min} min posle prozora" if s.late_min else ""
            lines.append(f"{c.name}: vozi {vlabel(problem, r.vehicle_id)}, dolazak oko {hhmm(s.start)}{late}.")
    if diff["newly_late"]:
        lines.append("Kasniće (preporuka: pozvati kupca unapred):")
        for x in diff["newly_late"]:
            c = problem.customers[problem.order(x["order_id"]).customer_id]
            lines.append(f"- {c.name}, {c.town}: dolazak {hhmm(x['arrival'])}, {x['late_min']} min posle prozora")
    if diff["newly_unserved"]:
        lines.append("Danas nije moguće isporučiti (predlog: prebaciti za sutra ili iznajmiti vozilo):")
        for u in diff["newly_unserved"]:
            c = problem.customers[u["customer_id"]]
            lines.append(f"- {c.name}, {c.town}: {u['reason']}")
    d = diff["delta"]
    lines.append(f"Trošak dana: {rsd(diff['old']['cost_rsd'])} → {rsd(diff['new']['cost_rsd'])} "
                 f"({'+' if d['cost_rsd'] >= 0 else ''}{rsd(d['cost_rsd'])}), kilometraža {signed(d['km'], ' km')}.")
    if not (diff["moved"] or diff["second_trips"] or diff["added"] or diff["newly_late"]
            or diff["newly_unserved"] or diff.get("removed")):
        lines.append("Plan se ne menja za preostale isporuke.")
    if diff["hard_violations"]:
        lines.append(f"PAŽNJA: plan ima {diff['hard_violations']} prekršaja ograničenja, ne primenjivati bez provere.")
    lines.append("Plan čeka odobrenje dispečera.")
    return "\n".join(lines)
