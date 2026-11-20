export type Kpis = {
  orders: number; served: number; unserved: number; vehicles_used: number; trips: number; km: number;
  crew_hours: number; cost_km_rsd: number; cost_crew_rsd: number; cost_fixed_rsd: number; cost_rsd: number;
  late_stops: number; late_minutes: number; on_time_pct: number; avg_load_pct: number; fuel_l: number;
  co2_kg: number; hard_violations: number; solve_s?: number;
};
export type Stop = { order_id: string; customer_id: string; arrival: number; start: number; depart: number;
  late_min: number; wait_min: number; onboard_dp_after: number; km_from_prev: number };
export type Trip = { depart: number; end: number; stops: Stop[]; km: number; load_dp: number; load_kg: number; pickup_dp: number };
export type Route = { vehicle_id: number; trips: Trip[]; broken_at: number | null; km: number; cost: number; start: number | null; end: number | null };
export type Plan = { method: string; routes: Route[]; unserved: { order_id: string; customer_id: string; reason: string }[];
  kpis: Kpis; violations: { type: string; detail: string }[] };
export type Customer = { id: string; name: string; type: string; town: string; lat: number; lon: number; access: number;
  key_account: boolean; order: null | { id: string; pallets: number; kg: number; empties: number; urgent: boolean; window: string } };
export type Vehicle = { id: number; kind: string; plate: string; capacity_kg: number; capacity_pallets: number; available: boolean };
export type Scenario = {
  scenario_id: number; label: string; status: string; clock: string;
  problem: { day: string; depot: { name: string; lat: number; lon: number }; vehicles: Vehicle[]; customers: Customer[] };
  plan: Plan; positions: Record<string, { lat: number; lon: number; state: string }>;
};
export type State = Scenario & { baseline_kpis: Kpis | null; llm: boolean; llm_provider: string | null; llm_model: string | null; pending: number; day_plan_kpis: Kpis; has_changes: boolean };
export type Diff = {
  moved: { order_id: string; customer: string; from_vehicle: number; to_vehicle: number }[];
  added: string[]; removed: { order_id: string; customer: string; vehicle: number }[];
  newly_late: { order_id: string; customer: string; town: string; vehicle: number; late_min: number; arrival: number }[];
  newly_unserved: { order_id: string; customer: string; reason: string }[];
  second_trips: { vehicle: number; depart: number; stops: number }[];
  delta: Record<string, number>; old: Record<string, number>; new: Record<string, number>; hard_violations: number;
};
export type Proposal = { id: number; created_at: string; status: string; source: string; created_by: string;
  decided_by: string | null; change: Record<string, unknown> & { kind: string; mode: string }; diff: Diff;
  explanation: string; scenario?: Scenario };
export type CommandResult = { answer: string; model: string | null; intent: Record<string, unknown> | null; proposal_ids: number[]; source: string;
  tool_calls: { name: string; input: unknown }[]; cost_usd: number; latency_ms: number };
export type Roi = {
  assumptions: Record<string, number>;
  per_day: Record<string, number>; per_year: Record<string, number>; route_cost_pct: number;
};
export type AuditEntry = { id: number; ts: string; actor: string; action: string; detail: Record<string, unknown>; hash: string };

let user = "lazar";
try { user = localStorage.getItem("dispatcher-user") || user; } catch { /* storage blocked */ }
export const getUser = () => user;
export const setUser = (u: string) => { user = u; try { localStorage.setItem("dispatcher-user", u); } catch { /* ignore */ } };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-User": user, ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  state: (clock: string) => req<State>(`/api/state?clock=${clock}`),
  baseline: (clock: string) => req<Scenario>(`/api/baseline?clock=${clock}`),
  command: (text: string, clock: string) => req<CommandResult>("/api/command", { method: "POST", body: JSON.stringify({ text, clock }) }),
  proposals: () => req<Proposal[]>("/api/proposals"),
  proposal: (id: number, clock: string) => req<Proposal>(`/api/proposals/${id}?clock=${clock}`),
  apply: (id: number) => req<{ status: string }>(`/api/proposals/${id}/apply`, { method: "POST" }),
  reject: (id: number) => req<{ status: string }>(`/api/proposals/${id}/reject`, { method: "POST" }),
  roi: (q: Record<string, number>) => req<Roi>(`/api/roi?${new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)]))}`),
  audit: () => req<{ valid: boolean; first_bad_id: number | null; entries: AuditEntry[] }>("/api/audit"),
  reset: (seed: number) => req<unknown>("/api/reset", { method: "POST", body: JSON.stringify({ seed, solve_seconds: 10 }) }),
};

export const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
export const toMin = (s: string) => { const [h, m] = s.split(":").map(Number); return h * 60 + (m || 0); };
export const rsd = (x: number) => `${Math.round(x).toLocaleString("sr-RS")} RSD`;
export const num = (x: number, d = 0) => x.toLocaleString("sr-RS", { maximumFractionDigits: d, minimumFractionDigits: d });
