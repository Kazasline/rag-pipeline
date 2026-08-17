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

# Words that ARE topical in general English but carry no discriminating power
# in THIS corpus, because construction and contract documents are built out of
# them. Round-3 reviewer R3-4: with a flat count of 2 shared terms, "what
# locations are shown for the security cameras?" was answered from rain-tree
# chunks on the strength of {locations, shown}, and "which contractor shall
# supply the pump?" came back SUPPORTED on {contractor, shall, supply}.
#
# This list is a FALLBACK. The real measure is document frequency taken from
# the index (see `discriminative_terms`), which adapts to the corpus instead of
# to my guesses about it. The list only applies when no index statistics are
# available — unit tests, and the first query after a rebuild.
DOMAIN_BOILERPLATE = {
    "shall", "will", "must", "may", "required", "require", "requirement",
    "requirements", "provide", "provided", "provision", "supply", "supplied",
    "install", "installed", "installation", "document", "documents",
    "drawing", "drawings", "project", "projects", "section", "clause",
    "detail", "details", "works", "work", "specification", "specifications",
    "spec", "specs", "refer", "reference", "note", "notes", "general",
    "applicable", "location", "locations", "shown", "show", "item", "items",
    "contractor", "consultant", "client", "employer", "sub-contractor",
    "subcontractor", "site", "area", "areas", "type", "types", "system",
    "systems", "date", "dated", "page", "sheet", "rev", "revision", "total",
    "including", "include", "included", "accordance", "relevant", "respective",
    "above", "below", "following", "hereby", "thereof", "said",
    # Malay equivalents
    "hendaklah", "perlu", "dibekalkan", "dipasang", "dokumen", "projek",
    "lukisan", "kerja", "kerja-kerja", "spesifikasi", "rujuk", "rujukan",
    "nota", "umum", "lokasi", "kawasan", "jenis", "tarikh", "muka", "surat",
}

# A term appearing in more than this share of indexed chunks tells you nothing
# about which chunk you want.
MAX_DF_RATIO = 0.25

# ...but a SHARE needs a population. Over 10 chunks, a term in 3 of them reads
# as "in 30% of the corpus" and gets discarded as boilerplate, when really the
# corpus is too small to have boilerplate. Below this many indexed chunks the
# measured ratio is noise and the fallback list is the more honest instrument —
# the same reasoning that stops the benchmark reporting a p95 from n=3.
MIN_DOCS_FOR_DF = 200

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w\-]{2,}")


def content_terms(text: str) -> set:
    """Topical terms only: alphanumeric tokens of 3+ chars, minus stopwords.

    Codes like `LAI-003` survive because the pattern keeps internal hyphens.
    """
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}


# Bare revision markers: R01, Rev A, R00. They appear in filenames and bodies
# across the whole corpus, so sharing one with the query says only "both
# mention a revision", never "this chunk answers the question".
_REV_TERM_RE = re.compile(r"^r(?:ev)?[-_. ]?\d{1,2}[a-z]?$", re.IGNORECASE)


def discriminative_terms(shared: set, doc_freq: dict | None = None,
                         total_docs: int = 0, exclude: set | None = None) -> set:
    """Of the terms shared between a query and a chunk, those that actually
    narrow the corpus.

    Preference order, most defensible first:
      1. MEASURED — a term in more than MAX_DF_RATIO of indexed chunks is
         dropped. This is derived from the corpus, so it adapts as the corpus
         changes and does not depend on anyone's intuition about the domain.
      2. FALLBACK — when no index statistics are available, drop the
         hand-listed domain boilerplate above.

    The fallback is strictly weaker and is labelled as such wherever its result
    is reported, so a floor decision made without corpus statistics is never
    presented as if it had them.

    `exclude` carries SCOPE terms — the project name the query already targets.
    Sharing the project name with a chunk is not evidence about the question:
    project scope is enforced separately by §60 isolation, and counting it
    twice let "In the Dawson project, what is the pump warranty on drawing
    L-201?" clear the floor on {dawson, l-201} while saying nothing about
    pumps (round-3 reviewer, follow-up to R3-2/R3-4).
    """
    out = {t for t in shared if not _REV_TERM_RE.match(t)}
    if exclude:
        out -= exclude
    # The boilerplate list ALWAYS applies. Measurement may only make the floor
    # stricter, never looser.
    #
    # Round-4 reviewer N4-1: this used to *replace* the list with the DF test
    # above MIN_DOCS_FOR_DF, so on the real 16,782-chunk index the list was
    # dead code and every boilerplate word whose corpus frequency happened to
    # fall under MAX_DF_RATIO was RESTORED as discriminating. Two are enough:
    # "which contractor shall supply the pump?" came back SUPPORTED from a
    # rain-tree chunk on {contractor, shall, supply}. The "upgrade" from list
    # to measurement was a loosening, and it re-opened the exact hole it was
    # written to close.
    out = {t for t in out if t not in DOMAIN_BOILERPLATE}
    if doc_freq is not None and total_docs >= MIN_DOCS_FOR_DF:
        out = {t for t in out
               if doc_freq.get(t, 0) / total_docs <= MAX_DF_RATIO}
    return out


def df_is_meaningful(total_docs: int) -> bool:
    return total_docs >= MIN_DOCS_FOR_DF
