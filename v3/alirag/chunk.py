r"""Document-aware hierarchical chunking (spec §29).

Extraction produces locator-tagged segments (page / sheet-range / slide /
section). Chunking preserves that hierarchy: a segment that fits becomes one
chunk; an oversized segment is split with overlap into child chunks that keep
the parent's page/locator, and the parent ordinal is recorded so context
construction can widen from a child hit to its siblings (source expansion).

Character-based sizing (V1-proven 1800/360) rather than tokenizer-based:
bge-m3 handles ~8k tokens, so 1800 chars is comfortably safe, and skipping
the tokenizer keeps ingestion CPU-cheap.
"""

from __future__ import annotations


def split_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = size - overlap
    out = []
    i = 0
    while i < len(text):
        piece = text[i:i + size]
        # prefer breaking on a paragraph/sentence boundary inside the tail 20%
        if i + size < len(text):
            cut = max(piece.rfind("\n\n", int(size * 0.8)),
                      piece.rfind(". ", int(size * 0.8)),
                      piece.rfind("\n", int(size * 0.8)))
            if cut > 0:
                piece = piece[:cut + 1]
        piece = piece.strip()
        if piece:
            out.append(piece)
        if i + size >= len(text):
            break
        i += max(len(piece) - overlap, step // 2, 1)
    return out


def chunk_segments(segments: list[dict], size: int = 1800,
                   overlap: int = 360) -> list[dict]:
    """Flatten extraction segments into ordered chunk dicts for
    Manifest.replace_chunks(). Parent/child links preserved via parent_ord."""
    chunks: list[dict] = []
    ordn = 0
    for s in segments:
        pieces = split_text(s["text"], size, overlap)
        if not pieces:
            continue
        if len(pieces) == 1:
            chunks.append({"ord": ordn, "level": s.get("level", "paragraph"),
                           "parent_ord": None, "page": s.get("page"),
                           "locator": s.get("locator", ""), "text": pieces[0]})
            ordn += 1
            continue
        parent_ord = ordn
        # parent = locator header only (keeps hierarchy without duplicating text)
        chunks.append({"ord": ordn, "level": "section", "parent_ord": None,
                       "page": s.get("page"), "locator": s.get("locator", ""),
                       "text": pieces[0]})
        ordn += 1
        for piece in pieces[1:]:
            chunks.append({"ord": ordn, "level": s.get("level", "paragraph"),
                           "parent_ord": parent_ord, "page": s.get("page"),
                           "locator": s.get("locator", ""), "text": piece})
            ordn += 1
    return chunks
