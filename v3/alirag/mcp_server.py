r"""MCP server exposing V3 to OpenClaw / WhatsApp (spec §53, §43).

Two tools, and the second one is the point:

  ali_ask(question)     — ask V3, get a grounded answer with citations.
  ali_mark(...)         — say whether that answer was RIGHT, and if not, which
                          document should have answered it.

`ali_mark` is what makes the benchmark reachable. §43 refuses to score a
question set that no human reviewed, and the review step was a spreadsheet the
operator has to fill in by hand — which is why it had not happened. Marking an
answer over WhatsApp is the same review, done at the moment the operator
already knows whether the answer was right, on a question they actually asked
rather than one anybody invented.

Each mark appends one row to the same CSV `bench run` reads, with
`reviewed=yes` — because a human just reviewed it. Nothing here can mark a
question reviewed on its own: `ali_mark` only ever records what the operator
said about an answer they saw.

PRIVACY (§2): this sends answer text and document names to WhatsApp, i.e.
through Meta's servers. That is the same trade V1 already makes in
`rag_mcp.py`; it is the operator's call, but it is a real one and it is stated
here rather than buried. The retrieval, the model and the index all stay local
— only the reply leaves the machine.

Run:  python -m alirag.mcp_server
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

from .answer import Engine
from .bench import CSV_COLUMNS
from .config import load_config

_cfg = load_config(os.environ.get("ALIRAG_CONFIG"))
_engine: Engine | None = None

# The last answer, so `ali_mark` knows what is being marked. One slot: this is
# a single-operator tool, and guessing which of several pending answers a
# "betul" refers to would fabricate the review §43 exists to require.
_last: dict = {}

LOG = Path(_cfg.dir("logs")) / "mcp_server.log"


def _log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except (OSError, TypeError):
        pass


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(_cfg)
    return _engine


def answer_question(question: str, mode: str = "") -> str:
    """Ask V3 and format the reply for a chat window."""
    eng = _get_engine()
    resp = eng.query(question, mode_override=(mode or None) and mode.upper())
    _last.clear()
    _last.update({
        "question": question,
        "status": resp["evidence_status"],
        "sources": resp.get("sources", []),
        "ts": time.time(),
    })
    _log(f"[ask] {question!r} -> {resp['evidence_status']}")

    lines = [resp["answer"], ""]
    if resp.get("sources"):
        lines.append("Sumber / sources:")
        for i, s in enumerate(resp["sources"][:5], 1):
            loc = s.get("location") or ""
            proj = s.get("project") or "?"
            src = s.get("project_source") or "?"
            mark = " (nama folder, bukan disahkan)" if src == "folder" else ""
            lines.append(f"  [{i}] {s['file']} {loc} — projek: {proj}{mark}")
    for f in resp.get("verifier_flags", [])[:3]:
        lines.append(f"! {f}")
    for c in resp.get("conflicts", [])[:2]:
        lines.append(f"!! {c}")

    if resp["evidence_status"] in ("SUPPORTED", "PARTIAL"):
        lines += ["", "Betul tak? Balas:  betul   /   salah <nama fail yang sepatutnya>",
                  "(Setiap kali kau jawab, satu soalan ujian terbina — itu yang "
                  "buat markah ketepatan jadi nyata.)"]
    return "\n".join(lines)


def mark_answer(correct: bool, should_be: str = "", project: str = "") -> str:
    """Record the operator's verdict on the last answer as a benchmark row.

    This is a HUMAN review — the operator saw the answer and judged it — which
    is exactly what §43 requires and what the spreadsheet step was for.
    """
    if not _last.get("question"):
        return ("Tiada jawapan untuk ditanda. Tanya satu soalan dulu.")

    srcs = _last.get("sources") or []
    expected = (should_be or "").strip()
    if not expected:
        if not correct:
            return ("Kalau salah, sebut fail mana yang SEPATUTNYA jawab. "
                    "Contoh:  salah Selgate Payment Cert 11.pdf")
        if not srcs:
            return "Jawapan tu tiada sumber, jadi tak boleh jadi soalan ujian."
        expected = srcs[0]["file"]
    proj = (project or "").strip() or (srcs[0].get("project") if srcs else "")
    if not proj or proj == "UNKNOWN":
        return ("Tak tahu projek mana untuk soalan ni. Sebut projek: "
                "contoh  salah Selgate Cert 11.pdf | SITE CONCEPT INTERNATIONAL")

    path = Path(_cfg.dir("benchmark")) / "questions.csv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_COLUMNS)
        w.writerow([_last["question"], expected, proj, "", "yes", "",
                    expected, "marked over chat"])
    n = sum(1 for _ in open(path, encoding="utf-8-sig")) - 1
    _log(f"[mark] correct={correct} expected={expected!r} rows={n}")

    verdict = "betul" if correct else f"salah -> {expected}"
    more = max(0, 5 - n)
    tail = (f" Lagi {more} soalan sebelum boleh run ujian." if more
            else " Dah cukup untuk run ujian: alirag bench run "
                 f'--questions "{path}" --label fast')
    return f"Direkod ({verdict}). Jumlah soalan ujian: {n}.{tail}"


def build() -> object:
    """Construct the FastMCP server. Imported lazily so the module can be
    tested, and the CLI used, without fastmcp installed."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("alirag-v3")

    @mcp.tool()
    def ali_ask(question: str, mode: str = "") -> str:
        """Search the firm's OWN project documents on E:\\ and answer from them
        with citations — tenders, BQs, claims, LAIs, drawings, letters, in
        English or Malay. Answers ONLY from the indexed documents; says so
        plainly when the documents do not contain the answer. `mode` is
        optional: FAST (default), DEEP, or FULLSWING for a thorough search."""
        return answer_question(question, mode)

    @mcp.tool()
    def ali_mark(correct: bool, should_be: str = "", project: str = "") -> str:
        """Record whether the LAST answer was right. Use when the user replies
        'betul'/'correct' or 'salah'/'wrong'. If wrong, `should_be` is the
        filename that should have answered. Each mark becomes one reviewed
        benchmark question — this is how retrieval accuracy gets measured."""
        return mark_answer(correct, should_be, project)

    @mcp.tool()
    def ali_status() -> str:
        """Index status: how many documents are indexed, which parts are
        measured, and how many benchmark questions have been marked so far."""
        eng = _get_engine()
        st = eng.mf.stats()
        path = Path(_cfg.dir("benchmark")) / "questions.csv"
        marked = (sum(1 for _ in open(path, encoding="utf-8-sig")) - 1
                  if path.exists() else 0)
        return json.dumps({
            "documents_indexed": st.get("state_INDEXED", 0),
            "chunks": st.get("chunks_total", 0),
            "project_unknown_pct": st.get("project_unknown_pct"),
            "benchmark_questions_marked": marked,
            "accuracy_measured": False if marked < 5 else "run bench to find out",
        }, indent=2)

    return mcp


if __name__ == "__main__":
    build().run()
