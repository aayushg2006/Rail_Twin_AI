"""Low-level extractors for the two Vasai Road timetable PDFs.

Both are India Rail Info exports. Neither encodes running days as plain text, so
each needs its own trick. `parse_pdfs.py --verify` cross-checks them.

* IRI-Departures-BSR-2.pdf (206 long-distance) - the 7 day letters are always
  drawn at fixed x, and RUNNING days are dark (0.251 grey) while non-running
  days are light (0.878). Colour is the encoding.
* Vasai Local Train data.pdf (458 EMU) - Sat/Sun are drawn bold when the train
  runs on them; weekday letters are regular either way. Weekday running days
  come from the text-layer token instead, aligned as a subsequence of SMTWTFS.

Neither PDF is read at runtime; parse_pdfs.py bakes the result into
data/timetable-bsr.json.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

CANON_DAYS = "SMTWTFS"

logging.disable(logging.WARNING)  # pdfminer font-descriptor noise on the local PDF


@dataclass
class RawService:
    number: str
    name: str
    rake_type: str
    dep_hhmm: str
    dest: str
    platform: int | None = None
    dest_platform: int | None = None
    arr_hhmm: str = ""
    duration: str = ""
    halts: int | None = None
    distance_km: float | None = None
    days: str = CANON_DAYS          # 7 chars, "." where the train does not run
    origin: str = "BSR"
    source: str = ""
    day_confidence: str = "HIGH"


def _hhmm(text: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mm:02d}" if h < 24 and mm < 60 else None


def _near(words, lo: float, hi: float):
    """Words whose left edge falls in [lo, hi), left to right."""
    return sorted((w for w in words if lo <= w["x0"] < hi), key=lambda w: w["x0"])


def _join(words) -> str:
    return " ".join(w["text"] for w in words).strip()


def _int(words) -> int | None:
    for w in words:
        if w["text"].isdigit():
            return int(w["text"])
    return None


def _num(words) -> float | None:
    for w in words:
        t = w["text"].replace(",", "")
        if re.fullmatch(r"\d+(\.\d+)?", t):
            return float(t)
    return None


# --------------------------------------------------------------- long distance
# Column anchors measured from the export (page width 595pt).
IRI_DAY_X = (231.0, 244.0, 261.0, 274.0, 291.0, 306.0, 321.0)
IRI_DARK = 0.55          # non-stroking grey below this = running day


def extract_long_distance(path: str) -> list[RawService]:
    import pdfplumber

    out: list[RawService] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(extra_attrs=["size"])
            chars = page.chars
            # Each train block ends with its day strip (size 8 at x=231); the
            # rest of the block sits in the ~20pt above it.
            day_rows = sorted(
                {round(c["top"], 1) for c in chars
                 if abs(c["x0"] - IRI_DAY_X[0]) < 2.5 and c["text"] == "S" and c["size"] < 8.5}
            )
            for day_top in day_rows:
                block = [w for w in words if day_top - 21 <= w["top"] < day_top - 1]
                if not block:
                    continue
                data = [w for w in block if w["x0"] >= 220]
                dep = next((_hhmm(w["text"]) for w in _near(data, 305, 335)
                            if _hhmm(w["text"])), None)
                num = next((w["text"] for w in _near(block, 55, 90)
                            if re.fullmatch(r"[A-Z0-9]{3,5}", w["text"])), None)
                if not dep or not num:
                    continue
                row_top = min((w["top"] for w in data), default=day_top)
                same = [w for w in data if abs(w["top"] - row_top) < 3]
                out.append(RawService(
                    number=num,
                    name=_join([w for w in sorted(block, key=lambda w: (w["top"], w["x0"]))
                                if 90 <= w["x0"] < 220]),
                    rake_type=_join(_near(same, 220, 256)) or "Exp",
                    dep_hhmm=dep,
                    dest=_join(_near(same, 335, 365)) or "?",
                    platform=_int(_near(same, 285, 300)),
                    dest_platform=_int(_near(same, 365, 380)),
                    arr_hhmm=next((_hhmm(w["text"]) for w in _near(same, 385, 415)
                                   if _hhmm(w["text"])), "") or "",
                    duration=_join(_near(same, 415, 462)),
                    halts=_int(_near(same, 462, 492)),
                    distance_km=_num(_near(same, 492, 535)),
                    days=_iri_days(chars, day_top),
                    source="IRI-Departures-BSR-2.pdf",
                ))
    return out


def _iri_days(chars, day_top: float) -> str:
    marks = []
    for x in IRI_DAY_X:
        ch = next((c for c in chars
                   if abs(c["top"] - day_top) < 2.0 and abs(c["x0"] - x) < 3.0
                   and c["text"] in "SMTWF"), None)
        if ch is None:
            marks.append(".")
            continue
        col = ch.get("non_stroking_color") or (0, 0, 0)
        grey = sum(col[:3]) / 3 if isinstance(col, (tuple, list)) else 0.0
        marks.append(ch["text"] if grey < IRI_DARK else ".")
    return "".join(marks)


# --------------------------------------------------------------------- suburban
LOCAL_DAY_X = (260.0, 267.2, 276.0, 283.1, 292.0, 299.1, 305.9)
LOCAL_ROW = re.compile(
    r"^(\d{5})(.{0,70}?)\s([SM][SMTWF \u00a0]{4,7})BSR\s*(\d\d:\d\d)([A-Z]{2,5})\s*(\d\d:\d\d)",
    re.M,
)


def extract_suburban(path: str) -> list[RawService]:
    import pdfplumber
    import pypdf

    # Weekday running days survive only in the text layer's spacing.
    text = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)
    tokens = {m.group(1): m.group(3) for m in LOCAL_ROW.finditer(text)}

    out: list[RawService] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(extra_attrs=["size"])
            by_row: dict[float, list] = {}
            for w in words:
                by_row.setdefault(round(w["top"] / 2) * 2, []).append(w)
            chars_by_row: dict[float, list] = {}
            for c in page.chars:
                chars_by_row.setdefault(round(c["top"] / 2) * 2, []).append(c)

            for key, row in by_row.items():
                num = next((w["text"] for w in _near(row, 40, 56)
                            if w["text"].isdigit() and len(w["text"]) == 5), None)
                if not num:
                    continue
                dep = next((_hhmm(w["text"]) for w in _near(row, 334, 350)
                            if _hhmm(w["text"])), None)
                if not dep:
                    continue
                bold = _local_weekend_bold(chars_by_row.get(key, []))
                days, conf = _local_days(tokens.get(num), bold)
                out.append(RawService(
                    number=num,
                    name=_join(_near(row, 60, 178)),
                    rake_type="Mumb",
                    dep_hhmm=dep,
                    dest=_join(_near(row, 356, 372)) or "?",
                    platform=_int(_near(row, 216, 232)),
                    arr_hhmm=next((_hhmm(w["text"]) for w in _near(row, 378, 396)
                                   if _hhmm(w["text"])), "") or "",
                    days=days,
                    day_confidence=conf,
                    source="Vasai Local Train data.pdf",
                ))
    return out


def _local_weekend_bold(chars) -> tuple[bool, bool]:
    """(sunday_runs, saturday_runs) - bold means the service runs that day."""
    def is_bold(x: float) -> bool:
        c = next((c for c in chars
                  if abs(c["x0"] - x) < 3.0 and c["text"] in "SMTWF"), None)
        return bool(c) and "Bold" in c["fontname"]
    return is_bold(LOCAL_DAY_X[0]), is_bold(LOCAL_DAY_X[6])


def _local_days(token: str | None, bold: tuple[bool, bool]) -> tuple[str, str]:
    """Align the text-layer day letters onto SMTWTFS as a left-greedy subsequence,
    then let the bold weekend flags decide slots 0 and 6."""
    sun, sat = bold
    if not token:
        marks = list(CANON_DAYS)
    else:
        marks = ["."] * 7
        i = 0
        for ch in (c for c in token if c in "SMTWF"):
            while i < 7 and CANON_DAYS[i] != ch:
                i += 1
            if i >= 7:
                break
            marks[i] = ch
            i += 1
    marks[0] = "S" if sun else "."
    marks[6] = "S" if sat else "."
    return "".join(marks), ("HIGH" if token else "ASSUMED")
