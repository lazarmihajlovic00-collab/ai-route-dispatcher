import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import basemap from "./basemap.json";
import {
  api, AuditEntry, CommandResult, Customer, getUser, hhmm, Kpis, num, Proposal, Roi, rsd, Scenario, setUser, State, toMin,
} from "./api";

const COLORS = ["#2f6bff", "#e8590c", "#2b8a3e", "#c2255c", "#7048e8", "#0b8a9c", "#b7791f", "#5c940d", "#9c36b5"];
const vcolor = (id: number) => COLORS[(id - 1) % COLORS.length];
const KIND: Record<string, string> = { kombi: "kombi 3,5 t", kamion_7_5t: "kamion 7,5 t", kamion_12t: "kamion 12 t" };
const TOWNS: [string, number, number][] = [
  ["Beograd", 44.8125, 20.4612], ["Novi Sad", 45.2671, 19.8335], ["Pančevo", 44.8708, 20.6403],
  ["Smederevo", 44.6628, 20.9300], ["Obrenovac", 44.6549, 20.2006], ["Lazarevac", 44.3800, 20.2570],
  ["Mladenovac", 44.4400, 20.6950], ["Inđija", 45.0480, 20.0820], ["Ruma", 45.0080, 19.8220],
  ["Stara Pazova", 44.9850, 20.1600],
];
const EXAMPLES = [
  "Kamion 1 je u kvaru, preraspodeli",
  "Hitna porudžbina za Market Ribar, 1 paleta, do 13h",
  "Restoran Zora je zatvoren danas",
  "Restoran Bela lađa može tek od 13 do 15h",
  "Kakav je pregled plana?",
];

type View = "active" | "baseline" | "proposal";
type Msg = { role: "user" | "assistant"; text: string; meta?: string; proposals?: number[] };

export default function App() {
  const [clock, setClock] = useState("10:00");
  const [state, setState] = useState<State | null>(null);
  const [baseline, setBaseline] = useState<Scenario | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [selected, setSelected] = useState<Proposal | null>(null);
  const [view, setView] = useState<View>("active");
  const [tab, setTab] = useState("dispatcher");
  const [highlight, setHighlight] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [user, setUserState] = useState(getUser());

  const refresh = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.state(clock), api.proposals()]);
      setState(s);
      setProposals(p);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, [clock]);

  useEffect(() => {
    const t = setTimeout(refresh, 120);
    return () => clearTimeout(t);
  }, [refresh]);

  useEffect(() => {
    if (view === "baseline") api.baseline(clock).then(setBaseline).catch(() => setBaseline(null));
    if (view === "proposal" && selected) api.proposal(selected.id, clock).then(setSelected).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, clock, selected?.id]);

  const shown: Scenario | null =
    view === "baseline" ? baseline : view === "proposal" && selected?.scenario ? selected.scenario : state;

  const openProposal = async (id: number) => {
    const p = await api.proposal(id, clock);
    setSelected(p);
    setView("proposal");
    setTab("dispatcher");
  };

  const decide = async (id: number, action: "apply" | "reject") => {
    try {
      await (action === "apply" ? api.apply(id) : api.reject(id));
      setSelected(null);
      setView("active");
      await refresh();
    } catch (e) {
      setError(String((e as Error).message));
      await refresh();
    }
  };

  if (!state) {
    return <div className="loading">{error ? `Greška: ${error}` : "Učitavam plan…"}</div>;
  }
  const kpis = shown?.plan.kpis ?? state.plan.kpis;
  const pending = proposals.filter((p) => p.status === "PENDING");

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <div className="logo" aria-hidden>
            <svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M3 6h11v9H3zM14 9h4l3 3v3h-7zM6.5 18.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm11 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z" /></svg>
          </div>
          <div>
            <h1>AI Dispečer ruta</h1>
            <p>Dunav Pića d.o.o. (izmišljena firma) · {state.problem.day} · {state.problem.depot.name}</p>
          </div>
        </div>
        <div className="clock">
          <label htmlFor="clock">Vreme u danu <strong>{clock}</strong></label>
          <input id="clock" type="range" min={330} max={990} step={5} value={toMin(clock)}
            onChange={(e) => setClock(hhmm(Number(e.target.value)))} />
        </div>
        <label className="user">Korisnik (odobrava predloge)
          <input value={user} onChange={(e) => { setUser(e.target.value); setUserState(e.target.value); }} />
        </label>
      </header>

      {error && <div className="error" role="alert">{error} <button className="link" onClick={() => setError(null)}>zatvori</button></div>}

      <KpiRow kpis={kpis} base={view === "active" && !state.has_changes ? state.baseline_kpis : null} />

      <div className="viewbar">
        <div className="seg" role="tablist" aria-label="Prikaz na mapi">
          <button className={view === "active" ? "on" : ""} onClick={() => setView("active")}>Aktivni plan</button>
          <button className={view === "baseline" ? "on" : ""} onClick={() => setView("baseline")}>Ručni plan (poređenje)</button>
          <button className={view === "proposal" ? "on" : ""} disabled={!selected} onClick={() => setView("proposal")}>
            Predlog{selected ? ` #${selected.id}` : ""}
          </button>
        </div>
        <span className="muted small">
          {view === "proposal" ? "Pregled predloga, aktivni plan se ne menja dok ga ne odobriš." :
            view === "baseline" ? "Teritorije + najbliži sused, svi kreću u 06:00 (heuristika ručnog planiranja)." :
              `Optimizovano: OR-Tools · AI dispečer: ${state.llm ? state.llm_model : "pravila (bez API ključa)"}`}
        </span>
      </div>

      <main className="grid">
        <section className="mapcard">
          {shown && <MapView scn={shown} highlight={highlight} onPick={setHighlight} />}
          <p className="note">Linije spajaju stanice redom (nisu putanje po putu). Pozadina: OpenStreetMap kada je dostupan, inače granice (Natural Earth).</p>
        </section>
        <aside className="side">
          <nav className="tabs">
            {[["dispatcher", "Dispečer"], ["vehicles", "Vozila"], ["compare", "Poređenje"], ["roi", "Biznis slučaj"], ["audit", "Audit"]].map(([k, l]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {l}{k === "dispatcher" && pending.length > 0 && <span className="pill">{pending.length}</span>}
              </button>
            ))}
          </nav>
          {tab === "dispatcher" && (
            <Dispatcher clock={clock} llm={state.llm} pending={pending} selected={selected}
              onResult={async (r) => { await refresh(); if (r.proposal_ids.length) await openProposal(r.proposal_ids[r.proposal_ids.length - 1]); }}
              onOpen={openProposal} onDecide={decide} problem={state.problem} />
          )}
          {tab === "vehicles" && shown && <Vehicles scn={shown} highlight={highlight} onPick={setHighlight} />}
          {tab === "compare" && <Compare base={state.baseline_kpis} opt={state.day_plan_kpis} onShow={() => setView("baseline")} />}
          {tab === "roi" && <RoiPanel />}
          {tab === "audit" && <Audit />}
        </aside>
      </main>
      {shown && <Timeline scn={shown} clock={toMin(clock)} highlight={highlight} onPick={setHighlight} />}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ KPIs */
function KpiRow({ kpis, base }: { kpis: Kpis; base: Kpis | null }) {
  const d = (k: keyof Kpis, better: "lower" | "higher", fmt: (x: number) => string) => {
    if (!base) return null;
    const diff = (kpis[k] as number) - (base[k] as number);
    if (Math.abs(diff) < 0.05) return <small>isto kao ručni plan</small>;
    const good = better === "lower" ? diff < 0 : diff > 0;
    return <small className={good ? "good" : "bad"}>{diff > 0 ? "+" : "−"}{fmt(Math.abs(diff))} vs ručni plan</small>;
  };
  return (
    <div className="kpis">
      <div className="kpi"><span>Trošak dana</span><strong>{rsd(kpis.cost_rsd)}</strong>{d("cost_rsd", "lower", (x) => rsd(x))}</div>
      <div className="kpi"><span>Kilometraža</span><strong>{num(kpis.km)} km</strong>{d("km", "lower", (x) => `${num(x)} km`)}</div>
      <div className="kpi"><span>Isporuke na vreme</span><strong>{num(kpis.on_time_pct, 1)}%</strong>
        {base ? d("on_time_pct", "higher", (x) => `${num(x, 1)} p.p.`) : <small>{kpis.late_stops} kasni</small>}</div>
      <div className="kpi"><span>Isporučeno</span><strong>{kpis.served} / {kpis.orders}</strong>
        <small className={kpis.unserved ? "bad" : ""}>{kpis.unserved ? `${kpis.unserved} danas nije moguće` : "sve porudžbine u planu"}</small></div>
      <div className="kpi"><span>Vozila / ture</span><strong>{kpis.vehicles_used} / {kpis.trips}</strong><small>popunjenost {num(kpis.avg_load_pct)}%</small></div>
      <div className="kpi"><span>CO₂ (dizel)</span><strong>{num(kpis.co2_kg)} kg</strong>{d("co2_kg", "lower", (x) => `${num(x)} kg`)}</div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------- map */
function MapView({ scn, highlight, onPick }: { scn: Scenario; highlight: number | null; onPick: (v: number | null) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true }).setView([44.86, 20.35], 9);
    L.geoJSON(basemap as GeoJSON.FeatureCollection, {
      style: (f) => ({ color: "#8a94a6", weight: 1, fillColor: f?.properties?.name === "Serbia" ? "#eef1f5" : "#f7f8fa", fillOpacity: 1 }),
      interactive: false,
    }).addTo(m);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© OpenStreetMap", opacity: 0.85,
    }).addTo(m);
    for (const [name, lat, lon] of TOWNS) {
      L.marker([lat, lon], { icon: L.divIcon({ className: "town", html: name, iconSize: [90, 14], iconAnchor: [45, -6] }), interactive: false }).addTo(m);
    }
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const g = layer.current;
    if (!g) return;
    g.clearLayers();
    const cust = new Map<string, Customer>(scn.problem.customers.map((c) => [c.id, c]));
    const depot: L.LatLngTuple = [scn.problem.depot.lat, scn.problem.depot.lon];
    for (const r of scn.plan.routes) {
      const on = highlight === null || highlight === r.vehicle_id;
      for (const t of r.trips) {
        const pts: L.LatLngTuple[] = [depot, ...t.stops.map((s) => [cust.get(s.customer_id)!.lat, cust.get(s.customer_id)!.lon] as L.LatLngTuple)];
        if (r.broken_at === null || t !== r.trips[r.trips.length - 1]) pts.push(depot);
        L.polyline(pts, { color: vcolor(r.vehicle_id), weight: highlight === r.vehicle_id ? 5 : 3, opacity: on ? 0.85 : 0.12 })
          .on("click", () => onPick(r.vehicle_id)).addTo(g);
        t.stops.forEach((s, i) => {
          const c = cust.get(s.customer_id)!;
          L.circleMarker([c.lat, c.lon], {
            radius: highlight === r.vehicle_id ? 7 : 5, color: s.late_min > 0 ? "#d92d20" : "#ffffff", weight: s.late_min > 0 ? 3 : 1.5,
            fillColor: vcolor(r.vehicle_id), fillOpacity: on ? 1 : 0.2,
          }).bindTooltip(`<b>${c.name}</b> (${c.town})<br>Vozilo ${r.vehicle_id}, stanica ${i + 1}<br>Dolazak ${hhmm(s.start)} · prozor ${c.order?.window ?? ""}`
            + `${s.late_min > 0 ? `<br><b>kasni ${s.late_min} min</b>` : ""}<br>${c.order ? `${num(c.order.pallets, 1)} pal. · ${c.order.kg} kg · ambalaža ${num(c.order.empties, 1)} pal.` : ""}`)
            .on("click", () => onPick(r.vehicle_id)).addTo(g);
        });
      }
    }
    for (const u of scn.plan.unserved) {
      const c = cust.get(u.customer_id);
      if (!c) continue;
      L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "unserved", html: "✕", iconSize: [18, 18] }) })
        .bindTooltip(`<b>${c.name}</b><br>Danas nije moguće: ${u.reason}`).addTo(g);
    }
    L.marker(depot, { icon: L.divIcon({ className: "depot", html: "DC", iconSize: [30, 22] }) }).bindTooltip(scn.problem.depot.name).addTo(g);
    for (const [vid, p] of Object.entries(scn.positions)) {
      const id = Number(vid);
      if (p.state === "u bazi" || p.state === "zavrsio") continue;
      const broken = p.state === "u kvaru";
      L.marker([p.lat, p.lon], {
        icon: L.divIcon({ className: `truck${broken ? " broken" : ""}`, html: `<span style="background:${broken ? "#d92d20" : vcolor(id)}">${id}</span>`, iconSize: [24, 24] }),
        zIndexOffset: 1000,
      }).bindTooltip(`Vozilo ${id}: ${p.state}`).on("click", () => onPick(id)).addTo(g);
    }
  }, [scn, highlight, onPick]);

  return <div className="map" ref={el} />;
}

/* ------------------------------------------------------------------------------------------- timeline */
function Timeline({ scn, clock, highlight, onPick }: { scn: Scenario; clock: number; highlight: number | null; onPick: (v: number | null) => void }) {
  const t0 = 330, t1 = 990, rowH = 26, left = 128, width = 1000;
  const x = (m: number) => left + ((m - t0) / (t1 - t0)) * (width - left - 8);
  const vehicles = scn.problem.vehicles;
  const cust = new Map(scn.problem.customers.map((c) => [c.id, c]));
  const h = vehicles.length * rowH + 30;
  return (
    <section className="timeline">
      <h3>Vremenska linija po vozilu</h3>
      <svg viewBox={`0 0 ${width} ${h}`} role="img" aria-label="Gantogram ruta">
        {Array.from({ length: 12 }, (_, i) => 360 + i * 60).filter((m) => m <= t1).map((m) => (
          <g key={m}>
            <line x1={x(m)} x2={x(m)} y1={0} y2={h - 18} className="grid" />
            <text x={x(m)} y={h - 4} className="tick" textAnchor="middle">{hhmm(m)}</text>
          </g>
        ))}
        {vehicles.map((v, i) => {
          const r = scn.plan.routes.find((rr) => rr.vehicle_id === v.id);
          const y = i * rowH + 4;
          const dim = highlight !== null && highlight !== v.id;
          return (
            <g key={v.id} opacity={dim ? 0.3 : 1} onClick={() => onPick(highlight === v.id ? null : v.id)} className="row">
              <text x={4} y={y + 15} className="label">{v.id}. {KIND[v.kind]}{!v.available ? " ✕" : ""}</text>
              {r?.trips.map((t, k) => (
                <g key={k}>
                  <rect x={x(t.depart)} y={y + 8} width={Math.max(1, x(t.end) - x(t.depart))} height={6} rx={3} fill={vcolor(v.id)} opacity={0.3} />
                  {t.stops.map((s) => (
                    <rect key={s.order_id} x={x(s.start)} y={y + 3} width={Math.max(2, x(s.depart) - x(s.start))} height={16} rx={2}
                      fill={vcolor(v.id)} stroke={s.late_min > 0 ? "#d92d20" : "none"} strokeWidth={2}>
                      <title>{`${cust.get(s.customer_id)?.name}: ${hhmm(s.start)}-${hhmm(s.depart)}${s.late_min ? `, kasni ${s.late_min} min` : ""}`}</title>
                    </rect>
                  ))}
                </g>
              ))}
              {r?.broken_at != null && <text x={x(r.broken_at)} y={y + 16} className="brk" textAnchor="middle">✕ kvar</text>}
            </g>
          );
        })}
        <line x1={x(clock)} x2={x(clock)} y1={0} y2={h - 18} className="now" />
      </svg>
    </section>
  );
}

/* ----------------------------------------------------------------------------------------- dispatcher */
function Dispatcher({ clock, llm, pending, selected, onResult, onOpen, onDecide, problem }: {
  clock: string; llm: boolean; pending: Proposal[]; selected: Proposal | null; problem: Scenario["problem"];
  onResult: (r: CommandResult) => Promise<void>; onOpen: (id: number) => void; onDecide: (id: number, a: "apply" | "reject") => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([{
    role: "assistant",
    text: "Napiši šta se desilo, npr. da je kamion u kvaru, da je stigla hitna porudžbina ili da kupac traži drugi termin. "
      + "Napraviću predlog novog plana sa objašnjenjem, a ti ga odobravaš.",
  }]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [msgs]);

  const send = async (t: string) => {
    if (!t.trim()) return;
    setMsgs((m) => [...m, { role: "user", text: t, meta: clock }]);
    setText("");
    setBusy(true);
    try {
      const r = await api.command(t, clock);
      const meta = r.source === "llm" ? `${r.model} · ${(r.latency_ms / 1000).toFixed(1)} s${r.cost_usd ? ` · $${r.cost_usd.toFixed(3)}` : ""}` : "pravila (bez AI)";
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, meta, proposals: r.proposal_ids }]);
      await onResult(r);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `Greška: ${(e as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dispatcher">
      <div className="log" ref={logRef} aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">{m.text}</div>
            {(m.meta || m.proposals?.length) && (
              <div className="meta">
                {m.role === "user" ? `u ${m.meta}` : m.meta}
                {m.proposals?.map((p) => <button key={p} className="link" onClick={() => onOpen(p)}>predlog #{p}</button>)}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant"><div className="bubble typing">Računam novi plan…</div></div>}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="npr. Kamion 3 je u kvaru" aria-label="Komanda dispečeru" disabled={busy} />
        <button className="primary" disabled={busy || !text.trim()}>Pošalji</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((e) => <button key={e} onClick={() => send(e)} disabled={busy}>{e}</button>)}
      </div>
      {!llm && <p className="muted small">Bez API ključa radi parser sa pravilima. Sa GEMINI_API_KEY (besplatno) ili ANTHROPIC_API_KEY komande tumači AI model.</p>}

      {selected && <ProposalCard p={selected} problem={problem} onDecide={onDecide} />}
      {pending.filter((p) => p.id !== selected?.id).length > 0 && (
        <div className="pending">
          <h3>Predlozi na čekanju</h3>
          {pending.filter((p) => p.id !== selected?.id).map((p) => (
            <button key={p.id} className="pending-item" onClick={() => onOpen(p.id)}>
              #{p.id} · {p.explanation.split("\n")[0]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ProposalCard({ p, problem, onDecide }: { p: Proposal; problem: Scenario["problem"]; onDecide: (id: number, a: "apply" | "reject") => void }) {
  const d = p.diff;
  const name = (cid: string) => problem.customers.find((c) => c.id === cid)?.name ?? cid;
  void name;
  const delta = (k: string, unit: string, lowerBetter = true) => {
    const v = d.delta[k];
    const cls = v === 0 ? "" : (v < 0) === lowerBetter ? "good" : "bad";
    return <dd className={cls}>{v > 0 ? "+" : ""}{k === "cost_rsd" ? rsd(v) : `${num(v)}${unit}`}</dd>;
  };
  return (
    <div className={`proposal ${p.status.toLowerCase()}`}>
      <div className="phead">
        <strong>Predlog #{p.id}</strong>
        <span className={`badge ${p.status === "PENDING" ? "warn" : p.status === "APPLIED" ? "ok" : "muted"}`}>{
          { PENDING: "čeka odobrenje", APPLIED: "primenjen", REJECTED: "odbijen", STALE: "zastareo" }[p.status] ?? p.status}</span>
        <span className="muted small">{p.change.mode === "intraday" ? "u toku dana" : "pre polaska"} · {p.source === "llm" ? "AI model" : "pravila"}</span>
      </div>
      <dl className="facts">
        <div><dt>Trošak</dt>{delta("cost_rsd", "")}</div>
        <div><dt>Kilometraža</dt>{delta("km", " km")}</div>
        <div><dt>Kasni</dt>{delta("late_stops", "", true)}</div>
        <div><dt>Neisporučeno</dt>{delta("unserved", "", true)}</div>
      </dl>
      <pre className="explain">{p.explanation}</pre>
      {d.hard_violations > 0 && <p className="bad">Plan ima prekršaje ograničenja.</p>}
      {p.status === "PENDING" ? (
        <div className="decide">
          <button className="primary" onClick={() => onDecide(p.id, "apply")}>Primeni plan</button>
          <button onClick={() => onDecide(p.id, "reject")}>Odbij</button>
          <span className="muted small">Odobrava: {getUser()}</span>
        </div>
      ) : <p className="muted small">Odlučio: {p.decided_by ?? "-"}</p>}
    </div>
  );
}

/* -------------------------------------------------------------------------------------------- vehicles */
function Vehicles({ scn, highlight, onPick }: { scn: Scenario; highlight: number | null; onPick: (v: number | null) => void }) {
  const cust = useMemo(() => new Map(scn.problem.customers.map((c) => [c.id, c])), [scn]);
  return (
    <div className="vehicles">
      {scn.problem.vehicles.map((v) => {
        const r = scn.plan.routes.find((x) => x.vehicle_id === v.id);
        const open = highlight === v.id;
        return (
          <div key={v.id} className={`vcard${open ? " open" : ""}`}>
            <button className="vhead" onClick={() => onPick(open ? null : v.id)} aria-expanded={open}>
              <span className="dot" style={{ background: vcolor(v.id) }} />
              <strong>Vozilo {v.id}</strong> <span className="muted">{KIND[v.kind]} · {v.plate}</span>
              <span className="right">{r ? `${r.trips.reduce((a, t) => a + t.stops.length, 0)} st. · ${num(r.km)} km · ${hhmm(r.start!)}-${hhmm(r.end!)}` : v.available ? "bez ture" : "van upotrebe"}</span>
            </button>
            {open && r && r.trips.map((t, k) => (
              <div key={k} className="trip">
                <div className="muted small">Tura {k + 1}: polazak {hhmm(t.depart)}, povratak {hhmm(t.end)} · utovar {num(t.load_dp / 10, 1)} pal. / {num(t.load_kg)} kg ({num(100 * Math.max(t.load_kg / v.capacity_kg, t.load_dp / 10 / v.capacity_pallets))}%)</div>
                <ol>
                  {t.stops.map((s) => {
                    const c = cust.get(s.customer_id)!;
                    return (
                      <li key={s.order_id}>
                        <span className="eta">{hhmm(s.start)}</span> {c.name} <span className="muted">· {c.town} · {c.order?.window}</span>
                        {s.late_min > 0 && <span className="badge block">kasni {s.late_min} min</span>}
                        {c.order?.urgent && <span className="badge warn">hitno</span>}
                      </li>
                    );
                  })}
                </ol>
              </div>
            ))}
          </div>
        );
      })}
      {scn.plan.unserved.length > 0 && (
        <div className="vcard"><div className="vhead"><strong>Danas nije moguće</strong></div>
          <ul>{scn.plan.unserved.map((u) => <li key={u.order_id}>{cust.get(u.customer_id)?.name}: {u.reason}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------------------------- compare */
function Compare({ base, opt, onShow }: { base: Kpis | null; opt: Kpis; onShow: () => void }) {
  if (!base) return <p>Nema ručnog plana za poređenje.</p>;
  const rows: [string, keyof Kpis, (x: number) => string, boolean][] = [
    ["Trošak dana", "cost_rsd", rsd, true], ["  gorivo i održavanje", "cost_km_rsd", rsd, true],
    ["  posada (vozač + pomoćnik)", "cost_crew_rsd", rsd, true], ["  fiksno po vozilu", "cost_fixed_rsd", rsd, true],
    ["Kilometraža", "km", (x) => `${num(x)} km`, true], ["Radni sati posade", "crew_hours", (x) => `${num(x, 1)} h`, true],
    ["Kasne isporuke", "late_stops", (x) => num(x), true], ["Ukupno kašnjenje", "late_minutes", (x) => `${num(x)} min`, true],
    ["Vozila", "vehicles_used", (x) => num(x), true], ["Prosečna popunjenost", "avg_load_pct", (x) => `${num(x)}%`, false],
    ["Gorivo", "fuel_l", (x) => `${num(x)} l`, true], ["CO₂", "co2_kg", (x) => `${num(x)} kg`, true],
  ];
  return (
    <div>
      <p className="lead">Jutarnji plan dana (pre poremećaja): iste porudžbine, ista vozila. Ručni plan je heuristika (teritorije po pravcu, najbliži sused, svi kreću u 06:00) koja poštuje kapacitete i zone, ali ne proverava prozore stanicu po stanicu.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Pokazatelj</th><th className="num">Ručni</th><th className="num">Optimizovan</th><th className="num">Razlika</th></tr></thead>
          <tbody>
            {rows.map(([label, k, f, lower]) => {
              const diff = (opt[k] as number) - (base[k] as number);
              const good = lower ? diff < 0 : diff > 0;
              return (
                <tr key={k}><td style={{ whiteSpace: "pre" }}>{label}</td><td className="num">{f(base[k] as number)}</td><td className="num">{f(opt[k] as number)}</td>
                  <td className={`num ${diff === 0 ? "" : good ? "good" : "bad"}`}>{diff > 0 ? "+" : diff < 0 ? "−" : ""}{f(Math.abs(diff))}</td></tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p><button onClick={onShow}>Prikaži ručni plan na mapi</button></p>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------- ROI */
function RoiPanel() {
  const [q, setQ] = useState({ working_days: 300, dispatcher_rsd_per_hour: 1200, disruptions_per_day: 1.5, manual_plan_min: 90, manual_replan_min: 35 });
  const [roi, setRoi] = useState<Roi | null>(null);
  useEffect(() => { const t = setTimeout(() => api.roi(q).then(setRoi).catch(() => setRoi(null)), 200); return () => clearTimeout(t); }, [q]);
  const field = (k: keyof typeof q, label: string, step = 1) => (
    <label key={k}>{label}<input type="number" step={step} value={q[k]} onChange={(e) => setQ({ ...q, [k]: Number(e.target.value) })} /></label>
  );
  return (
    <div className="roi">
      <p className="lead">Ušteda na rutama je izmerena simulatorom (optimizovan jutarnji plan vs ručni plan, iste porudžbine). Vreme dispečera je pretpostavka koju možeš da menjaš.</p>
      <div className="form">
        {field("working_days", "Radnih dana godišnje")}
        {field("dispatcher_rsd_per_hour", "Trošak dispečera (RSD/h)", 50)}
        {field("disruptions_per_day", "Poremećaja dnevno", 0.5)}
        {field("manual_plan_min", "Ručno planiranje dana (min)", 5)}
        {field("manual_replan_min", "Ručna preraspodela (min)", 5)}
      </div>
      {roi && (
        <>
          <div className="big">
            <span>Procena uštede godišnje</span>
            <strong>{rsd(roi.per_year.total_rsd)}</strong>
            <small>≈ {num(roi.per_year.total_eur)} EUR · rute {num(roi.route_cost_pct, 1)}% jeftinije</small>
          </div>
          <dl className="facts">
            <div><dt>Rute, godišnje</dt><dd>{rsd(roi.per_year.route_cost_rsd)}</dd></div>
            <div><dt>Vreme dispečera</dt><dd>{rsd(roi.per_year.dispatcher_rsd)} · {num(roi.per_year.dispatcher_hours)} h</dd></div>
            <div><dt>Manje km godišnje</dt><dd>{num(roi.per_year.km)} km</dd></div>
            <div><dt>Manje CO₂</dt><dd>{num(roi.per_year.co2_t, 1)} t</dd></div>
            <div><dt>Kasnih isporuka manje (dnevno)</dt><dd>{roi.per_day.late_stops_avoided}</dd></div>
          </dl>
        </>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------------------------------------- audit */
function Audit() {
  const [a, setA] = useState<{ valid: boolean; entries: AuditEntry[] } | null>(null);
  useEffect(() => { api.audit().then(setA).catch(() => setA(null)); }, []);
  if (!a) return <p>Učitavam…</p>;
  return (
    <div>
      <p className={a.valid ? "good" : "bad"}>{a.valid ? "Lanac heševa je ispravan: zapisi nisu menjani." : "Lanac heševa je prekinut: neko je menjao zapise!"}</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Vreme (UTC)</th><th>Ko</th><th>Akcija</th><th>Detalj</th></tr></thead>
          <tbody>
            {a.entries.map((e) => (
              <tr key={e.id}><td className="small">{e.ts.slice(11, 19)}</td><td>{e.actor}</td><td>{e.action}</td>
                <td className="small mono">{JSON.stringify(e.detail).slice(0, 120)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
