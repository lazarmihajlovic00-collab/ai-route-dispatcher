"""AI dispatcher: understands requests in Serbian, calls the planning tools, explains the result.

Manual tool-use loop, on the Anthropic Messages API or on Gemini function calling (same tools, same
system prompt, same approval rules). The model never computes routes or numbers itself: routes come
from OR-Tools, numbers from the simulator, and every change is a proposal that waits for a person.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .. import llm, service
from ..domain import hhmm, parse_hhmm
from ..store import Store

MAX_TURNS = 8

SYSTEM_PROMPT = """Ti si AI asistent dispečera u distribuciji pića (Dunav Pića d.o.o., distributivni centar
Šimanovci, 9 vozila: 1-2 kamioni 12 t, 3-6 kamioni 7,5 t, 7-9 kombiji). Dispečer piše kratko, na srpskom,
često bez dijakritika. Odgovaraš na srpskom (latinica), jasno i kratko.

Kako radiš:
- Rute računa optimizator (OR-Tools), a brojeve (troškovi, km, kašnjenja) daje simulator. Ti ne računaš
  rute ni iznose napamet: svaki broj u odgovoru mora poticati iz rezultata alata.
- Pre nego što predložiš promenu za kupca, nađi ga alatom pronadji_kupca. Ako dva kupca podjednako
  odgovaraju, pitaj koji je u pitanju umesto da pogađaš.
- Kvar ili vozilo koje ne može da krene: predlozi_kvar. Hitna ili dodatna porudžbina:
  predlozi_hitnu_porudzbinu. Kupac otkazao ili zatvoren: predlozi_otkazivanje. Kupac traži drugi termin:
  predlozi_promenu_prozora. Za pitanja o planu: pregled_plana i ruta_vozila.
- Ako vreme nije navedeno, važi trenutno vreme (clock) iz poruke.
- Alati predlozi_* samo prave PREDLOG novog plana. Plan se ne menja dok ga dispečer ne odobri u aplikaciji.
  Nikad ne tvrdi da je plan promenjen.

Kad dobiješ predlog, napiši objašnjenje za dispečera:
1. jedna rečenica: šta se desilo i šta predlog radi;
2. ko preuzima koje isporuke (vozila, dodatne ture i vreme polaska);
3. ko kasni i koliko, uz preporuku da se kupac pozove unapred; šta danas ne može da se isporuči;
4. promena troška dana i kilometraže;
5. na kraju: "Predlog #N čeka tvoje odobrenje."
Bez uvoda i bez ponavljanja pitanja."""


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_NN = {"type": ["number", "null"]}

TOOLS = [
    _tool("pregled_plana", "Pregled aktivnog plana: KPI (trošak, km, kašnjenja), stanje svakog vozila, neisporučeno.",
          {}, []),
    _tool("ruta_vozila", "Stanice jednog vozila po redu sa vremenima dolaska.", {"vozilo": {"type": "integer"}},
          ["vozilo"]),
    _tool("pronadji_kupca", "Pretraga kupaca po nazivu i mestu. Vraća do 5 kandidata sa ocenom podudaranja, "
          "da li kupac danas ima porudžbinu i koje vozilo ga vozi.", {"upit": _S}, ["upit"]),
    _tool("predlozi_kvar", "Vozilo je u kvaru ili ne može da krene. Pravi predlog novog plana. vreme je HH:MM "
          "kada se kvar desio, null = trenutno vreme.", {"vozilo": {"type": "integer"}, "vreme": _NS},
          ["vozilo", "vreme"]),
    _tool("predlozi_hitnu_porudzbinu", "Nova hitna porudžbina za postojećeg kupca. Pravi predlog novog plana. "
          "palete je količina u paletama (decimalno), kg null = procena 550 kg po paleti, prozor_od/prozor_do "
          "HH:MM ili null = uobičajeni prozor kupca.",
          {"kupac_id": _S, "palete": {"type": "number"}, "kg": _NN, "povratna_ambalaza_palete": _NN,
           "prozor_od": _NS, "prozor_do": _NS},
          ["kupac_id", "palete", "kg", "povratna_ambalaza_palete", "prozor_od", "prozor_do"]),
    _tool("predlozi_otkazivanje", "Kupac je otkazao današnju isporuku ili je zatvoren. Pravi predlog novog plana.",
          {"kupac_id": _S}, ["kupac_id"]),
    _tool("predlozi_promenu_prozora", "Kupac danas može da primi robu samo u drugom terminu. od/do su HH:MM, do "
          "može biti null. Pravi predlog novog plana.", {"kupac_id": _S, "od": _S, "do": _NS},
          ["kupac_id", "od", "do"]),
]
TOOLS[-1]["cache_control"] = {"type": "ephemeral"}


@dataclass
class DispatchResult:
    answer: str
    intent: dict | None = None
    proposal_ids: list[int] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    source: str = "llm"
    cost_usd: float = 0.0
    latency_ms: int = 0
    proposal: dict | None = None


def _t(x: str | None) -> int | None:
    return parse_hhmm(x) if x else None


def tool_to_intent(name: str, args: dict) -> dict | None:
    if name == "predlozi_kvar":
        return {"intent": "breakdown", "vehicle": args["vozilo"], "time": _t(args.get("vreme"))}
    if name == "predlozi_hitnu_porudzbinu":
        return {"intent": "add_order", "customer": args["kupac_id"], "pallets": args["palete"], "kg": args.get("kg"),
                "empties": args.get("povratna_ambalaza_palete"), "tw_start": _t(args.get("prozor_od")),
                "tw_end": _t(args.get("prozor_do"))}
    if name == "predlozi_otkazivanje":
        return {"intent": "cancel", "customer": args["kupac_id"]}
    if name == "predlozi_promenu_prozora":
        return {"intent": "set_window", "customer": args["kupac_id"], "tw_start": _t(args["od"]),
                "tw_end": _t(args.get("do"))}
    return None


def _execute(store: Store, name: str, args: dict, clock: int, user: str, result: DispatchResult) -> dict:
    scn, problem, plan = service.current(store)
    try:
        if name == "pregled_plana":
            return service.overview(problem, plan)
        if name == "ruta_vozila":
            return service.route_view(problem, plan, int(args["vozilo"]))
        if name == "pronadji_kupca":
            return {"kandidati": service.find_customers(problem, plan, args["upit"])}
        intent = tool_to_intent(name, args)
        if intent is None:
            return {"error": f"nepoznat alat {name}"}
        if result.intent is None:
            result.intent = intent
        p = service.propose(store, intent, clock, "llm", f"agent (za {user})")
        result.proposal_ids.append(p["proposal_id"])
        result.proposal = p
        d = p["diff"]
        return {"proposal_id": p["proposal_id"], "status": "PENDING (čeka odobrenje dispečera)", "mode": p["mode"],
                "change": p["change"], "delta": d["delta"], "old": d["old"], "new": d["new"],
                "moved": d["moved"], "second_trips": [{**t, "depart": hhmm(t["depart"])} for t in d["second_trips"]],
                "newly_late": [{**x, "arrival": hhmm(x["arrival"])} for x in d["newly_late"]],
                "newly_unserved": d["newly_unserved"], "facts_text": p["explanation"]}
    except service.ServiceError as e:
        return {"error": str(e)}


def run(store: Store, command: str, clock: int, user: str = "dispecer") -> DispatchResult:
    if llm.provider() == "gemini":
        result = _run_gemini(store, command, clock, user)
    else:
        result = _run_anthropic(store, command, clock, user)
    if result.intent is None:
        names = [c["name"] for c in result.tool_calls]
        if "ruta_vozila" in names:
            result.intent = {"intent": "route", "vehicle": result.tool_calls[names.index("ruta_vozila")]["input"].get("vozilo")}
        elif "pregled_plana" in names:
            result.intent = {"intent": "overview"}
        else:
            result.intent = {"intent": "unknown"}
    return result


def _run_anthropic(store: Store, command: str, clock: int, user: str, history: list | None = None) -> DispatchResult:
    model = llm.DISPATCH_MODEL
    scn, problem, _ = service.current(store)
    messages = list(history or []) + [{"role": "user", "content": f"(clock: {hhmm(clock)}, dan: {problem.day})\n{command}"}]
    result = DispatchResult(answer="")
    t0 = time.perf_counter()
    for _ in range(MAX_TURNS):
        t_call = time.perf_counter()
        resp = llm.client().beta.messages.create(
            model=model, max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT}],
            tools=TOOLS, messages=messages,
            output_config={"effort": llm.DISPATCH_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        u = resp.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cost = llm.cost_usd(model, u.input_tokens, u.output_tokens, cache_read)
        result.cost_usd += cost
        store.log_llm(model, u.input_tokens, u.output_tokens, cache_read, cost,
                      int((time.perf_counter() - t_call) * 1000))
        if resp.stop_reason == "refusal":
            result.answer = "Model je odbio ovaj zahtev."
            break
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not tool_uses:
            result.answer = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            break
        outs = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
            out = _execute(store, tu.name, args, clock, user, result)
            result.tool_calls.append({"name": tu.name, "input": args})
            outs.append({"type": "tool_result", "tool_use_id": tu.id,
                         "content": json.dumps(out, ensure_ascii=False, default=str),
                         **({"is_error": True} if "error" in out else {})})
        messages.append({"role": "user", "content": outs})
    else:
        result.answer = "Nisam završio u dozvoljenom broju koraka."
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result


# ------------------------------------------------------------------------------------------------ gemini
def _gemini_schema(schema: dict) -> dict:
    """JSON Schema (strict tools) -> Gemini's OpenAPI subset: nullable instead of type lists, no
    additionalProperties."""
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = non_null[0].upper()
        if "null" in t:
            out["nullable"] = True
    elif t:
        out["type"] = t.upper()
    if "description" in schema:
        out["description"] = schema["description"]
    if "properties" in schema:
        out["properties"] = {k: _gemini_schema(v) for k, v in schema["properties"].items()}
        if schema.get("required"):
            out["required"] = list(schema["required"])
    return out


def gemini_tools() -> list[dict]:
    decls = []
    for t in TOOLS:
        d = {"name": t["name"], "description": t["description"]}
        if t["input_schema"].get("properties"):
            d["parameters"] = _gemini_schema(t["input_schema"])
        decls.append(d)
    return [{"functionDeclarations": decls}]


def _run_gemini(store: Store, command: str, clock: int, user: str) -> DispatchResult:
    import os

    model = llm.GEMINI_MODEL
    _, problem, _ = service.current(store)
    contents = [{"role": "user", "parts": [{"text": f"(clock: {hhmm(clock)}, dan: {problem.day})\n{command}"}]}]
    result = DispatchResult(answer="")
    t0 = time.perf_counter()
    url = f"{llm.GEMINI_URL}/models/{model}:generateContent"
    for _ in range(MAX_TURNS):
        t_call = time.perf_counter()
        resp = llm.gemini_post(url, {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents, "tools": gemini_tools(),
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        })
        if resp.status_code >= 400:
            result.answer = f"Gemini greška {resp.status_code}: {resp.text[:300]}"
            break
        body = resp.json()
        usage = body.get("usageMetadata", {})
        store.log_llm(model, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0),
                      usage.get("cachedContentTokenCount", 0), 0.0, int((time.perf_counter() - t_call) * 1000))
        cands = body.get("candidates") or []
        if not cands or "content" not in cands[0]:
            reason = (cands[0].get("finishReason") if cands else None) or body.get("promptFeedback", {}).get("blockReason")
            result.answer = f"Model nije dao odgovor ({reason})."
            break
        content = cands[0]["content"]
        contents.append(content)  # keep parts as returned (thought signatures must go back unchanged)
        calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
        if not calls:
            result.answer = "\n".join(p["text"] for p in content.get("parts", [])
                                      if "text" in p and not p.get("thought")).strip()
            break
        responses = []
        for call in calls:
            args = call.get("args") or {}
            for key in ("vozilo",):
                if key in args and args[key] is not None:
                    args[key] = int(args[key])
            out = _execute(store, call["name"], args, clock, user, result)
            result.tool_calls.append({"name": call["name"], "input": args})
            fr = {"name": call["name"], "response": json.loads(json.dumps(out, ensure_ascii=False, default=str))}
            if "id" in call:
                fr["id"] = call["id"]
            responses.append({"functionResponse": fr})
        contents.append({"role": "user", "parts": responses})
    else:
        result.answer = "Nisam završio u dozvoljenom broju koraka."
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result
