r"""Shared term analysis for the lexical index and the verifier (spec §39).

One stopword list, used in both places, deliberately.

The verifier used to own a private copy while `sparse._fts_query` used none.
That split is what let the round-2 reviewer reproduce a fabrication: the FTS
query was an OR of every token, so `"warranty" OR "period" OR "for" OR "the"
OR "pump"` matched documents containing only `the`, the sparse leg returned
rows, and the verifier read "the sparse leg matched" as "a term was matched"
and waived its own relevance floor. Two components disagreeing about what
counts as a word is a defect the tests could not see, because each was tested
against its own definition.
"""

from __future__ import annotations

import re

# Function words carry no topical signal, in either language this corpus uses.
STOPWORDS = {
    # English — short forms included for the FTS side, which tokenizes raw
    # words; content_terms() drops sub-3-character tokens by length anyway.
    "is", "of", "in", "to", "a", "an", "on", "at", "it", "as", "by", "be",
    "or", "if", "do", "we", "i", "my", "me", "so", "no", "up",
    "the", "and", "for", "are", "was", "were", "with", "this", "that", "from",
    "have", "has", "had", "not", "but", "you", "your", "our", "their", "its",
    "what", "which", "who", "whom", "when", "where", "why", "how", "all", "any",
    "can", "will", "would", "should", "could", "there", "here", "than", "then",
    "into", "onto", "out", "off", "over", "under", "about", "been", "being",
    "does", "did", "done", "get", "got", "per", "via", "such", "some", "each",
    "more", "most", "other", "only", "own", "same", "too", "very", "just",
    # Malay
    "yang", "dan", "untuk", "adalah", "ialah", "dengan", "ini", "itu", "dari",
    "daripada", "pada", "ada", "tidak", "tak", "atau", "juga", "akan", "boleh",
    "apa", "mana", "siapa", "bila", "kenapa", "mengapa", "bagaimana", "semua",
    "saya", "anda", "kita", "kami", "mereka", "dalam", "oleh", "kepada",
    "sebagai", "telah", "sudah", "masih", "lagi", "sahaja", "cari", "berapa",
    "jumlah", "senarai",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w\-]{2,}")


def content_terms(text: str) -> set:
    """Topical terms only: alphanumeric tokens of 3+ chars, minus stopwords.

    Codes like `LAI-003` survive because the pattern keeps internal hyphens.
    """
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}
