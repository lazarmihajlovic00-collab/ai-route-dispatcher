"""Business case: what the optimizer and the AI dispatcher save per year.

Two sources of saving, kept separate so each assumption can be challenged on its own:
1. Route cost: optimized plan vs the baseline plan on the same orders (km, crew hours, vehicles), measured
   by the simulator. Late deliveries are reported, not priced.
2. Dispatcher time: building the daily plan and re-planning after disruptions by hand vs reviewing a
   proposal. These minutes are assumptions, not measurements, and are editable in the dashboard.
"""

from __future__ import annotations


def business_case(baseline: dict, optimized: dict, working_days: int = 300, dispatcher_rsd_per_hour: float = 1200,
                  disruptions_per_day: float = 1.5, manual_plan_min: float = 90, manual_replan_min: float = 35,
                  tool_plan_min: float = 15, tool_replan_min: float = 4) -> dict:
    route_saving_day = baseline["cost_rsd"] - optimized["cost_rsd"]
    km_saving_day = baseline["km"] - optimized["km"]
    fuel_saving_day = baseline["fuel_l"] - optimized["fuel_l"]
    co2_saving_day = baseline["co2_kg"] - optimized["co2_kg"]
    minutes_saved_day = (manual_plan_min - tool_plan_min) + disruptions_per_day * (manual_replan_min - tool_replan_min)
    dispatcher_saving_day = minutes_saved_day / 60 * dispatcher_rsd_per_hour
    total_day = route_saving_day + dispatcher_saving_day
    return {
        "assumptions": {"working_days": working_days, "dispatcher_rsd_per_hour": dispatcher_rsd_per_hour,
                        "disruptions_per_day": disruptions_per_day, "manual_plan_min": manual_plan_min,
                        "manual_replan_min": manual_replan_min, "tool_plan_min": tool_plan_min,
                        "tool_replan_min": tool_replan_min},
        "per_day": {"route_cost_rsd": round(route_saving_day), "km": round(km_saving_day, 1),
                    "fuel_l": round(fuel_saving_day, 1), "co2_kg": round(co2_saving_day, 1),
                    "dispatcher_minutes": round(minutes_saved_day), "dispatcher_rsd": round(dispatcher_saving_day),
                    "total_rsd": round(total_day),
                    "late_stops_avoided": baseline["late_stops"] - optimized["late_stops"]},
        "per_year": {"route_cost_rsd": round(route_saving_day * working_days),
                     "dispatcher_rsd": round(dispatcher_saving_day * working_days),
                     "total_rsd": round(total_day * working_days),
                     "total_eur": round(total_day * working_days / 117.2),
                     "km": round(km_saving_day * working_days), "co2_t": round(co2_saving_day * working_days / 1000, 1),
                     "dispatcher_hours": round(minutes_saved_day * working_days / 60)},
        "route_cost_pct": round(100 * route_saving_day / baseline["cost_rsd"], 1) if baseline["cost_rsd"] else 0.0,
    }
