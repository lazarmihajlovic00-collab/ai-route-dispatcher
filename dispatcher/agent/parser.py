"""Rule-based parser for dispatcher commands in Serbian (Latin, with or without diacritics).

It is the fallback when no API key is set, and the baseline the LLM dispatcher is measured against.
It understands the four changes (breakdown, urgent order, cancellation, new time window) and two read
requests (plan overview, route of one vehicle). Anything else is "unknown".
"""

from __future__ import annotations

import re

from ..service import norm

VEHICLE = r"(?:kamion\w*|vozil\w*|kombi\w*|auto\w*|tur\w*)\s*(?:br\.?|broj)?\s*(\d{1,2})\b"
TIME = r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:h\b|casova|sati)?"

BREAKDOWN = r"kvar|pokvar|ne radi|crk|ne moze da krene|ne krece|van funkcije|udes|udar|nesrec|zaglav|probu[sv]|stao|stalo|otkaz\w* motor|ispao iz"
ADD = r"hitn\w*|dodaj\w*|ubaci\w*|nov\w* porud|jos jedn\w*|dodatn\w*"
CANCEL = r"otkaz\w*|zatvoren\w*|ne treba\w*|odusta\w*|stornir\w*|ponist\w*|ne prima\w* danas|preskoc\w*"
WINDOW = r"tek od|tek posle|samo od|samo posle|moze od|mogu od|prima od|pomer\w*|promen\w* (?:prozor|termin|vreme)|novi (?:prozor|termin)|posle podne|izmedju"
OVERVIEW = r"pregled|kako izgleda|koliko kosta|stanje|ukupn\w*|kpi|rezime|plan za danas"
ROUTE = r"ruta|rutu|gde je|stanice|redosled"

STOP = {"za", "kod", "u", "od", "do", "je", "danas", "molim", "te", "hitno", "hitna", "hitnu", "porudzbina",
        "porudzbinu", "isporuka", "isporuku", "paleta", "palete", "pal", "kg", "i", "sa", "na", "da", "se",
        "ce", "nam", "mu", "im", "joj", "ga", "treba", "trebaju", "dodaj", "ubaci", "otkazi", "otkazao",
        "otkazala", "otkazali", "zatvoren", "zatvorena", "zatvoreno", "ne", "prima", "moze", "tek", "samo",
        "pomeri", "promeni", "prozor", "termin", "vreme", "kupac", "kupca", "kupcu", "nova", "novu", "jos",
        "jednu", "dodatnu", "posle", "pre", "oko", "casova", "sati", "h", "ujutru", "popodne", "podne",
        "nije", "nisu", "ovo", "to", "tu", "ali", "pa", "a", "neka", "dostava", "dostavu", "isporuci",
        "dostavi", "porudzbine", "narudzbina", "narudzbinu", "hitan", "stornirao", "stornirala", "storniraj",
        "odustao", "odustala", "preskoci", "zove", "zvao", "zvala", "javio", "javila", "kaze", "rekao",
        "rekla", "isporuke", "povratne", "ambalaze", "ambalaza", "gajbi", "buradi"}


def _time(h: str, m: str | None) -> int | None:
    hh, mm = int(h), int(m or 0)
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh * 60 + mm
    return None


def _customer_phrase(text: str) -> str | None:
    t = re.sub(VEHICLE, " ", text)
    t = re.sub(r"\d+(?:[.,:]\d+)?\s*(?:pal\w*|kg|h\b|min\w*)?", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    words = [w for w in t.split() if w not in STOP and len(w) > 1]
    return " ".join(words) or None


def parse(command: str) -> dict:
    raw = command.strip()
    t = norm(raw)
    vm = re.search(VEHICLE, t)
    vehicle = int(vm.group(1)) if vm else None

    if vehicle is not None and re.search(BREAKDOWN, t):
        tm = re.search(r"(?:\bu|\bod|\boko|\bposle|\bvec od)\s+" + TIME, t)
        when = _time(tm.group(1), tm.group(2)) if tm else None
        return {"intent": "breakdown", "vehicle": vehicle, "time": when}

    if re.search(ADD, t) and re.search(r"porud|isporuk|narudz|dostav|pal|kg", t):
        pm = re.search(r"(\d+(?:[.,]\d+)?)\s*pal", t)
        km = re.search(r"(\d+)\s*kg", t)
        em = re.search(r"(\d+(?:[.,]\d+)?)\s*pal\w*\s*(?:povratn|ambala|prazn)", t)
        win = re.search(r"(?:od|izmedju)\s+" + TIME + r"\s*(?:do|i)\s+" + TIME, t)
        until = re.search(r"\bdo\s+" + TIME, t)
        intent = {"intent": "add_order", "customer": _customer_phrase(t),
                  "pallets": float(pm.group(1).replace(",", ".")) if pm else None,
                  "kg": int(km.group(1)) if km else None,
                  "empties": float(em.group(1).replace(",", ".")) if em else None,
                  "tw_start": None, "tw_end": None}
        if win:
            intent["tw_start"], intent["tw_end"] = _time(win.group(1), win.group(2)), _time(win.group(3), win.group(4))
        elif until:
            intent["tw_end"] = _time(until.group(1), until.group(2))
        return intent

    if re.search(CANCEL, t) and not re.search(WINDOW, t):
        return {"intent": "cancel", "customer": _customer_phrase(t)}

    if re.search(WINDOW, t) or re.search(r"\bod\s+" + TIME + r"\s*do\s+" + TIME, t):
        win = re.search(r"(?:od|izmedju|posle)\s+" + TIME + r"(?:\s*(?:do|i)\s+" + TIME + r")?", t)
        ts = te = None
        if win:
            ts = _time(win.group(1), win.group(2))
            if win.group(3):
                te = _time(win.group(3), win.group(4))
        elif "posle podne" in t:
            ts = 12 * 60
        return {"intent": "set_window", "customer": _customer_phrase(t), "tw_start": ts, "tw_end": te}

    if vehicle is not None and re.search(ROUTE, t):
        return {"intent": "route", "vehicle": vehicle}
    if re.search(OVERVIEW, t):
        return {"intent": "overview"}
    return {"intent": "unknown"}
