# Decision log

Short records of the design decisions behind this project: the question, the options, what was chosen and why.
Lazar Mihajlović owns the project (problem, scope, goals, final say). The implementation was built with Claude
(AI) as the builder. Where a decision was a builder recommendation that Lazar accepted, it says so.

## D0. Problem and scope (Lazar, 2026-11-19)
**Question:** Which second portfolio project shows optimization and AI for logistics, without being a toy
"shortest path" demo?
**Chosen:** A daily route planner for a beverage distributor around Belgrade, with a real optimizer
(vehicle capacities, customer time windows, costs) and an AI dispatcher on top that handles disruptions such as
"kamion 3 je u kvaru, preraspodeli" and explains the new plan. It matches the route-planning work at beverage
and FMCG distributors and logistics companies Lazar is applying to.
**Rejected:** a single-vehicle shortest-route demo, and a chatbot that "plans" routes by itself (see D2).

## D1. Optimizer: Google OR-Tools in Python (recommendation, accepted)
**Options:** (a) OR-Tools routing solver, (b) a hand-written heuristic, (c) a commercial MIP solver (Gurobi).
**Chosen:** (a). It is free, used in industry, handles capacities, time windows, heterogeneous fleets, optional
orders with penalties and custom constraints, and gives good solutions in seconds with guided local search.
**Trade-off:** results depend on the time limit and are not proven optimal. The evaluation reports the time limit
and compares against a baseline instead of claiming optimality.

## D2. The LLM never plans routes (builder design)
The LLM turns a dispatcher's sentence into a structured change and explains the result. OR-Tools computes the
routes, and the simulator (`plan.evaluate`) computes every time, load, kilometre and dinar. The model is told it may
use only numbers that came from a tool. **Why:** routing is a combinatorial problem that a language model does
badly and cannot verify, and dispatch decisions have to be repeatable and checkable.

## D3. A person approves every plan change (recommendation, accepted)
Every change, whether it came from the AI dispatcher, the rule parser or the API, becomes a *proposal* with a diff
and an explanation. Only a named person can apply it (`X-User`, and names starting with `agent` are refused). If the
active plan changed in the meantime, the proposal is marked stale and refused. Every step is written to a
hash-chained audit log. **Why:** drivers, the warehouse and customers act on the plan; a wrong automatic change costs
more than the minute it takes to click "Primeni".

## D4. Re-planning during the day (builder design)
**Rules:** stops already delivered stay delivered; a truck already on the road keeps its goods and its sequence
(only cancelled stops are removed); open work (stops of a broken truck, new orders, stops that no longer fit a new
window) goes to *second trips* from the depot, by vehicles that never left or after they return and reload
(30 minutes). Time windows become soft: being late costs 150 RSD per minute, up to 2 hours. Anything that still
does not fit is reported as "not possible today" with a reason.
**Why:** this is what a dispatcher can actually do at 10:30. Re-optimizing all trucks from scratch would move goods
that are already loaded on other trucks. "Late, with a phone call to the customer" is usually better than "not
delivered", so the plan shows who will be late and by how much.
**Simplification:** goods for re-planned stops are assumed to be in the warehouse (the broken truck is not unloaded).

## D5. Returnable empties are part of the load (builder design)
Beverage trucks deliver full crates and kegs and take empties back. The model tracks pallet places on board after
each stop (load at start minus deliveries plus pickups), not just the delivered total. Without this a plan can look
feasible and still not have room for the empties. A test proves the solver changes the visiting order because of it.

## D6. Before departure, re-plan with as few changes as possible (builder design)
Before the trucks leave, the whole day is solved again, warm-started from the current plan, and moving an order
to another vehicle costs 400 RSD in the objective (re-picking, paperwork, telling drivers). Without this, a small
change can reshuffle half the fleet for a saving of a few dinars.

## D7. Distances: approximation by default, road network as an option (recommendation, accepted)
**Options:** (a) straight-line distance times 1.32 with a speed profile by leg length, (b) OSRM road network.
**Chosen:** (a) by default so the project runs anywhere without a map server, with (b) built in
(`DISTANCE_PROVIDER=osrm`). Both planners use the same matrix, so the comparison between them is fair, but absolute
kilometres and times are approximations until OSRM is used. Map lines connect stops in order and are not road paths.

## D8. Cost model (builder assumptions, documented)
Per kilometre by vehicle type (fuel, tyres, maintenance: 30, 52, 72 RSD/km), crew cost per hour including a helper
on trucks (1.150 or 2.100 RSD/h), fixed cost per vehicle used per day (4.000 to 9.500 RSD). The numbers are
plausible for Serbia in 2026 but are assumptions; they live in `data.py` and a company would replace them with its
own.

## D9. What the optimizer is compared against (builder design)
The baseline imitates manual planning: territories by direction from the depot, vehicles filled one by one until
weight, pallets or shift run out, nearest-neighbour order, everyone leaves at 06:00. It respects capacity and access
zones (a dispatcher knows them) but does not check each time window. It is a reasonable heuristic, not a straw man,
and it is a proxy: the real number needs a company's historical plans.

## D10. LLM provider: Gemini or Claude through one setting (Lazar, 2026-11-19)
Lazar asked to use his free Gemini API key instead of a paid Anthropic key. The dispatcher supports both with the
same tools, prompt and approval rules: `LLM_PROVIDER=gemini|anthropic`, or automatically by whichever key is set.
Claude goes through the Anthropic SDK (tool use, strict schemas); Gemini through its REST function-calling API (no
extra dependency). Without any key, a rule parser handles the common commands; it is also the baseline in the
evaluation.

## D11. One simulator is the single source of truth (builder design)
Every planner outputs only a skeleton (vehicle, trips, order sequence, departure time). `plan.evaluate` computes all
times, loads, costs and violations from it. KPIs, diffs, explanations and tests all read the same numbers, and the
tests re-check constraints independently.

## D12. Storage: SQLite with versions and an audit chain (builder design)
Each plan is a stored version (active, superseded, proposed, rejected, baseline), so every change can be traced and
compared. SQLite is enough for one depot; the same tables work on PostgreSQL.
