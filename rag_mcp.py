r"""MCP server exposing the local file-RAG as a first-class `file_rag` tool for OpenClaw.

Returns the best TEXT matches and ALSO pushes the matching PDF page image(s) directly to the
user's WhatsApp via `openclaw message send --media` (OpenClaw does NOT forward MCP image
content to the channel, so we deliver the images ourselves). Rendering is bounded by a hard
time budget so the tool always returns quickly. Fully local."""
import sys, os, time, threading, hashlib, subprocess
sys.path.insert(0, r"P:\RAG Database\pipeline")
import query                                   # noqa: E402
from query import RENDER_MAX_PX               # noqa: E402
from mcp.server.fastmcp import FastMCP        # noqa: E402

LOGF = r"P:\RAG Database\pipeline\rag_mcp.log"
RENDER_BUDGET = 25     # seconds; if rendering exceeds this, return text now
SEND_TIMEOUT  = 25     # seconds per image send
MAX_IMAGES    = 2      # images pushed per query

# Image delivery: OpenClaw drops MCP tool-result images, so we send them ourselves via the CLI.
NODE  = r"C:\Program Files\nodejs\node.exe"
OCLAW = r"C:\Users\User\AppData\Roaming\npm\node_modules\openclaw\openclaw.mjs"
# Who receives the page images. Defaults to the primary on-site user; override per-call via the
# tool's reply_to argument, or globally via the RAG_REPLY_TO env var.
DEFAULT_REPLY_TO = os.environ.get("RAG_REPLY_TO", "+60125020189")
SESSIONS_JSON = r"C:\Users\User\.openclaw\agents\rag\sessions\sessions.json"

_sent = {}   # img_path -> last-sent epoch, to avoid resending the same page within a short window


def _current_recipient():
    """Who to send images to = the WhatsApp peer of the most-recently-active session. The current
    turn updates its session's timestamp, so the max-lastInteractionAt direct session IS the user
    asking right now. Model-independent — works for whichever number messaged."""
    try:
        import json
        with open(SESSIONS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        best, best_ts = None, -1
        for key, val in data.items():
            if ":whatsapp:direct:+" not in key:
                continue
            num = key.split(":whatsapp:direct:")[-1].strip()
            ts = val.get("lastInteractionAt") or val.get("updatedAt") or 0
            if ts > best_ts:
                best, best_ts = num, ts
        return best
    except Exception as e:
        log(f"  recipient lookup err: {e}")
        return None


def log(m):
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(m + "\n")
    except Exception:
        pass


mcp = FastMCP("file-rag")

# Pre-warm the embedding model so the first real query isn't slowed by an Ollama model load.
try:
    import rag
    log(f"[startup] VEC_PATH={rag.VEC_PATH} exists={os.path.exists(rag.VEC_PATH)} vec_count={rag.vec_count()}")
    rag.embed(["warmup"])
    log("[startup] embeddings warmed")
    log(f"[startup] recipient probe -> {_current_recipient()} (None = cannot read sessions.json)")
except Exception as e:
    log(f"[startup] warm failed: {e}")


def _render(results, box):
    try:
        box["imgs"] = query.render_pages(results, max_imgs=MAX_IMAGES)
    except Exception as e:
        box["err"] = str(e)


def _caption_for(img_path, results):
    """Map a rendered PNG back to its source document name + page for the WhatsApp caption."""
    base = os.path.basename(img_path)
    for r in results:
        if r.get("ext") != ".pdf" or not r.get("page"):
            continue
        h = hashlib.md5(r["path"].encode("utf-8")).hexdigest()[:10]
        if base == f"{h}_p{r['page']}_x{RENDER_MAX_PX}.png":
            return f"{r['name']} (p{r['page']})"
    return "Matching document page"


SENDLOG = r"P:\RAG Database\pipeline\rag_send.log"
# Windows flags: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — the send must outlive this tool
# call and run AFTER the turn returns. Sending THROUGH the gateway synchronously here deadlocks:
# the gateway is busy awaiting this very tool, so it can't service the send (-> 25s timeout).
_DETACHED = 0x00000008 | 0x00000200


def _send_images(imgs, results, reply_to):
    """Fire-and-forget each page image to the user's WhatsApp via a detached `message send`.
    Returns immediately so the tool/turn can finish and free the gateway to deliver the sends."""
    launched, now = 0, time.time()
    for p in imgs:
        if _sent.get(p, 0) > now - 120:        # dedup: same page already sent in last 2 min
            log(f"  send skip (recent) {os.path.basename(p)}")
            continue
        cap = _caption_for(p, results)
        try:
            lf = open(SENDLOG, "a", encoding="utf-8")
            lf.write(f"\n=== {time.strftime('%H:%M:%S')} send {os.path.basename(p)} -> {reply_to}\n")
            lf.flush()
            subprocess.Popen(
                [NODE, OCLAW, "message", "send", "--channel", "whatsapp",
                 "--target", reply_to, "--media", p, "--force-document", "--message", cap],
                stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                creationflags=_DETACHED, close_fds=True)
            _sent[p] = now
            launched += 1
            log(f"  send launched {os.path.basename(p)}")
        except Exception as e:
            log(f"  send EXC {os.path.basename(p)}: {e}")
    return launched


@mcp.tool()
def file_rag(question: str, k: int = 5, reply_to: str = ""):
    """Search the user's OWN local project documents (the firm's P: drive) — PDFs, drawings,
    plans, method statements, signed letters, claims, CPC checklists, tender/BQ docs, in English
    and Malay — and return the most relevant text snippets. The matching document PAGE IMAGES are
    sent to the user's WhatsApp AUTOMATICALLY by this tool. USE THIS for ANY request to find a
    document, ask what a document/drawing says, or to see/show a plan/letter/checklist. You do
    NOT need to read or open any file afterwards — the page images are already delivered.
    `reply_to` is optional (the user's E.164 WhatsApp number); leave blank to use the default."""
    t0 = time.time()
    rt = (reply_to or _current_recipient() or DEFAULT_REPLY_TO).strip()
    log(f"[call] q={question!r} k={k} reply_to={rt}")
    results = query.search(question, k=k)
    t_s = time.time() - t0
    log(f"  search {t_s:.1f}s results={len(results)}")
    if not results:
        return "No matching documents were found in the local index."

    files = query.group_by_file(results)
    lines = [f"Found {len(files)} matching document(s) for: {question}", ""]
    for f in files[:k]:
        pages = f" (pages {', '.join(str(p) for p in f['pages'])})" if f["pages"] else ""
        lines.append(f"- {f['name']}{pages}  [score {f['best_score']}]")
        snip = (f["snippets"][0] if f["snippets"] else "").strip().replace("\n", " ")
        lines.append(f"    {snip[:350]}")

    has_pdf_page = any(r.get("ext") == ".pdf" and r.get("page") for r in results)
    box = {}
    th = threading.Thread(target=_render, args=(results, box), daemon=True)
    th.start()
    th.join(RENDER_BUDGET)
    if th.is_alive():
        log(f"  render TIMEOUT >{RENDER_BUDGET}s")
        lines.append("\n(Page image still rendering from storage — ask again in a moment.)")
    else:
        imgs = box.get("imgs", [])
        log(f"  render {time.time()-t0-t_s:.1f}s imgs={len(imgs)} err={box.get('err')}")
        n = _send_images(imgs, results, rt)
        if n > 0:
            lines.append(f"\n({n} matching page image(s) sent to your WhatsApp.)")
        elif has_pdf_page and imgs:
            lines.append("\n(Could not deliver the page image — see rag_mcp.log.)")
        elif not has_pdf_page:
            lines.append("\n(These matches are spreadsheet/Word files, so there is no page "
                         "image to show — the key details are in the text above.)")
    log(f"[done] total {time.time()-t0:.1f}s")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
