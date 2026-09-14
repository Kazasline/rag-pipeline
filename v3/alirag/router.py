r"""Mode router (spec §8–§16, §55–§57).

Deterministic, LLM-free routing — FAST must not pay an LLM round-trip just to
be classified (§9). Precedence:

  1. explicit user mode command        (cepat/fast/deep/fullswing/phd ...)
  2. query complexity                  (multi-hop / research signals -> FULLSWING)
  3. document/corpus intent            (interpretation verbs -> DEEP)
  4. default FAST

Also detects exact-ID queries (LAI-003, L-201 R03 ...) so FAST can take the
exact-lexical shortcut, and extracts an explicit project mention for
project-isolation filtering (§60).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sparse import harvest_ids

FAST_TRIGGERS = [
    "cepat", "fast", "quick", "quickly", "ringkas", "segera", "terus",
    "straight", "simple answer", "short answer", "rapid", "speed", "fast mode",
    "laju", "pantas",
]
DEEP_TRIGGERS = ["deep", "mendalam", "teliti", "semak betul", "deep check"]
FULLSWING_TRIGGERS = [
    "fullswing", "full swing", "phd", "phd level", "research level",
    "deep research", "investigate everything", "exhaustive",
    "comprehensive analysis", "analyse habis", "analisa habis",
    "study everything", "find every evidence", "prove this",
    "cross check everything", "kaji semua", "siasat semua",
]

# document-interpretation intent -> DEEP (§12, §56)
DEEP_INTENT = [
    r"\bwhat does (this|the) (document|drawing|contract|spec|letter|email)\b",
    r"\bwhy (did|was|were|do)\b", r"\bcompare\b", r"\bbandingkan\b",
    r"\bcheck our previous\b", r"\bwhich clause\b", r"\bbased on (these|the|this)\b",
    r"\bwhat changed\b", r"\bposition\b", r"\brejected\b", r"\bexplain\b",
    r"\bkenapa\b", r"\bmengapa\b", r"\binterpret\b", r"\bjustif\w+\b",
    r"\bsupports?\b.*\bclause\b",
]

# genuine multi-hop / research complexity -> FULLSWING (§14, §57)
FULLSWING_INTENT = [
    r"\b(complete|entire|full) (history|timeline|chronology)\b",
    r"\breconstruct\b", r"\bcontradict\w*\b", r"\bprove\b", r"\bdisprove\b",
    r"\bevery (revision|document|email|drawing)\b",
    r"\bacross (all|every) projects?\b", r"\ball projects\b",
    r"\bcausal\b", r"\broot cause\b.*\bclaim\b", r"\bsemua (projek|dokumen)\b",
]

# Explicit permission to answer from more than one project at once (§60).
#
# This single switch disables the AMBIGUOUS_PROJECT guard, so it must be a
# DELIBERATE instruction and nothing else. Round-3 reviewer R3-1: the list
# previously held `\bcompare\b.*\bprojects?\b` — a greedy `.*` across the whole
# query — and `\bany project\b`, so ordinary questions consented on the user's
# behalf:
#
#   "compare the rain tree diameter with the turf spec in this project"  -> merged
#   "compare revision R00 and R01 of the tender for the project"         -> merged
#   "does any project document mention a defects liability period?"      -> merged
#
# The last one is not even about multiple projects. Dawson's and Meridian's
# claim amounts were then packed into one prompt, labelled "as asked".
#
# Every pattern here is now an ANCHORED phrase that a person can only write on
# purpose. When in doubt the system asks — that path exists and works.
CROSS_PROJECT_INTENT = [
    r"\bacross (all|every|multiple|both|the) projects\b",
    r"\b(for|from|in|over) all (the )?projects\b",
    r"\ball projects\b", r"\bevery project\b",
    r"\bcompare (all|the|these|both|multiple) projects\b",
    r"\bcompare projects\b", r"\bproject[- ]by[- ]project\b",
    r"\bmerentas (semua )?projek\b", r"\bsemua projek\b",
    r"\bsetiap projek\b", r"\bbanding\w* (semua|antara) projek\b",
]

# leading command forms: "fast:", "cepat -", "deep check ...", "phd:"
_CMD_RE = re.compile(
    r"^\s*(?P<cmd>[a-z ]{3,24}?)\s*[:\-—]\s*(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)


@dataclass
class Route:
    mode: str                       # FAST | DEEP | FULLSWING
    reason: str
    cleaned_query: str
    explicit: bool = False
    exact_ids: list = field(default_factory=list)
    project_hint: str | None = None
    cross_project: bool = False     # user explicitly asked across projects (§60)


def _match_trigger(text: str, triggers: list[str]) -> str | None:
    t = text.lower()
    for trig in triggers:
        if re.search(rf"(?<![a-z]){re.escape(trig)}(?![a-z])", t):
            return trig
    return None


def route(query: str, known_projects: list[str] | None = None) -> Route:
    q = query.strip()
    cleaned = q
    explicit_mode, reason = None, ""

    # 1. explicit leading command ("cepat: berapa...", "phd: prove ...")
    m = _CMD_RE.match(q)
    if m:
        cmd = m.group("cmd").lower().strip()
        if _match_trigger(cmd, FULLSWING_TRIGGERS):
            explicit_mode, reason, cleaned = "FULLSWING", f"explicit command '{cmd}'", m.group("rest")
        elif _match_trigger(cmd, DEEP_TRIGGERS):
            explicit_mode, reason, cleaned = "DEEP", f"explicit command '{cmd}'", m.group("rest")
        elif _match_trigger(cmd, FAST_TRIGGERS):
            explicit_mode, reason, cleaned = "FAST", f"explicit command '{cmd}'", m.group("rest")

    # 1b. explicit trigger anywhere in the query (natural language, §55)
    if explicit_mode is None:
        trig = _match_trigger(q, FULLSWING_TRIGGERS)
        if trig:
            explicit_mode, reason = "FULLSWING", f"explicit trigger '{trig}'"
        else:
            trig = _match_trigger(q, FAST_TRIGGERS)
            if trig:
                explicit_mode, reason = "FAST", f"explicit trigger '{trig}'"
            else:
                # bare "deep ..." prefix without colon
                if re.match(r"^\s*deep\b", q, re.IGNORECASE):
                    explicit_mode, reason = "DEEP", "explicit 'deep' prefix"

    exact_ids = harvest_ids(cleaned, limit=6)
    project_hint = _project_hint(cleaned, known_projects or [])
    # Judged on the ORIGINAL query: an explicit command prefix strips text from
    # `cleaned`, and "all projects" must not be lost with it.
    cross = any(re.search(rx, q.lower()) for rx in CROSS_PROJECT_INTENT)
    common = {"exact_ids": exact_ids, "project_hint": project_hint,
              "cross_project": cross}

    if explicit_mode:
        return Route(explicit_mode, reason, cleaned.strip(), explicit=True, **common)

    # 2. complexity -> FULLSWING
    ql = cleaned.lower()
    for rx in FULLSWING_INTENT:
        if re.search(rx, ql):
            return Route("FULLSWING", f"complexity signal /{rx}/", cleaned, **common)

    # 3. document interpretation -> DEEP
    for rx in DEEP_INTENT:
        if re.search(rx, ql):
            return Route("DEEP", f"document-intent signal /{rx}/", cleaned, **common)

    # 4. default FAST (exact locators and simple lookups belong here, §56)
    return Route("FAST", "default (simple corpus lookup)", cleaned, **common)


def _project_hint(query: str, known_projects: list[str]) -> str | None:
    """Exact, conservative project matching (§60): only claim a project when
    its name appears verbatim (case-insensitive) in the query."""
    ql = query.lower()
    best = None
    for p in known_projects:
        pl = p.lower().strip()
        if len(pl) >= 4 and pl in ql:
            if best is None or len(pl) > len(best):
                best = p
    return best
