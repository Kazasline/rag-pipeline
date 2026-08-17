#!/usr/bin/env python3
r"""Executable revert matrix — run this instead of believing a claim about it.

WHY THIS EXISTS
---------------
For three consecutive audits the builder asserted "every fix was individually
reverted and its test observed to fail", and for three consecutive audits the
independent reviewer found that untrue. The round-5 reviewer asked for the
matrix as an artifact it could re-run rather than re-derive. This is that
artifact.

RULE: ONE MUTATION, ONE MECHANISM.
A mutation that disables more than one thing at a time is not evidence about
any of them. The round-6 reviewer found this concretely: the F5-5 mutation
rewrote `sensitive = _sensitive_evidence(kept) or bool(` to `False and bool(`,
killing the query regex AND the money check AND the date check together. It
reported RED for the money half while the date half had no test at all. Any
mutation that cannot be traced to a single mechanism must be split.

Each MUTATION below disables exactly one fix by rewriting one fragment of
source. The script applies it to a scratch copy of the repo, runs the full test
suite there, and records whether the suite went red. A mutation that leaves the
suite GREEN is an unguarded fix — the code may be correct, but nothing would
notice if it stopped being.

The working tree is never modified: everything happens under a temporary copy.

    python v3/tools/revert_matrix.py                 # run all
    python v3/tools/revert_matrix.py --only F5-1     # run a subset
    python v3/tools/revert_matrix.py --json out.json # machine-readable

Exit status is non-zero if any mutation leaves the suite green, so this can
gate a commit.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (id, finding, file, old fragment, new fragment)
#
# Keep the fragments minimal and unique. If one stops matching, the fix was
# rewritten and the mutation needs updating — that is a signal, not a nuisance.
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # ---------------------------------------------------------- relevance floor
    ("F5-1", "project-name-only evidence can be SUPPORTED",
     "alirag/verify.py",
     "            if not good - project_terms:",
     "            if False:"),
    ("N4-1", "measurement loosens instead of tightening the floor",
     "alirag/terms.py",
     "    out = {t for t in out if t not in DOMAIN_BOILERPLATE}\n"
     "    if doc_freq is not None and total_docs >= MIN_DOCS_FOR_DF:",
     "    if doc_freq is not None and total_docs >= MIN_DOCS_FOR_DF:\n"
     "        return {t for t in out if doc_freq.get(t, 0) / total_docs <= MAX_DF_RATIO}\n"
     "    out = {t for t in out if t not in DOMAIN_BOILERPLATE}\n"
     "    if False:"),
    ("F5-6", "document frequencies never reach the verifier (wiring, not helper)",
     "alirag/answer.py",
     "        df, ndocs = self._term_stats(r.cleaned_query)",
     "        df, ndocs = None, 0"),
    ("N4-8", "_term_stats helper itself disabled",
     "alirag/answer.py",
     "            return self.sparse.doc_freq(content_terms(query)), total",
     "            return None, 0"),
    ("F5-6b", "the 'unmeasured' disclosure can claim measurement it never had",
     "alirag/terms.py",
     "    return total_docs >= MIN_DOCS_FOR_DF",
     "    return True"),
    ("R3-3", "floor gates the set instead of filtering per item",
     "alirag/verify.py",
     "            if on_topic:",
     "            if True:"),
    ("R3-12a", "stopwords not removed from content terms",
     "alirag/terms.py",
     "    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}",
     "    return set(_TOKEN_RE.findall(text.lower()))"),
    ("R3-12b", "a single shared term is enough",
     "alirag/verify.py", "MIN_CONTENT_OVERLAP = 2", "MIN_CONTENT_OVERLAP = 1"),
    ("N4-4", "naming a file certifies arbitrary content as SUPPORTED",
     "alirag/verify.py",
     "            or any(f.startswith(NAME_ONLY_FLAG) for f in flags)\n", "\n"),
    ("N4-5", "project label stripped term-by-term instead of as a phrase",
     "alirag/verify.py",
     "        scoped_query = query\n        if project_hint:",
     "        scoped_query = query\n        if False:"),
    ("R3-2a", "revision tokens count as document identifiers",
     "alirag/sparse.py",
     "    if REV_TOKEN_RE.match(tok):\n        return False",
     "    if False:\n        return False"),
    ("R3-2b", "a code mentioned in the body certifies the document",
     "alirag/verify.py",
     "    return bool(qcodes & code_variants(evidence.get(\"filename\", \"\") or \"\", limit=20))",
     "    return bool(qcodes & code_variants("
     "f\"{evidence.get('filename','')} {evidence.get('text','')}\", limit=200))"),
    ("F5-2", "code variants not indexed, so the exact leg misses real filenames",
     "alirag/sparse.py",
     "            for norm in code_variants(r[\"text\"]):",
     "            for norm in {normalize_id(x) for x in harvest_ids(r[\"text\"])}:"),
    ("F5-2b", "filename code variants not indexed",
     "alirag/sparse.py",
     "            for norm in code_variants(r[\"filename\"]):",
     "            for norm in {normalize_id(x) for x in harvest_ids(r[\"filename\"])}:"),
    ("N4-3b", "total code-variant bound removed (index-time work unbounded)",
     "alirag/sparse.py",
     "        if len(out) >= MAX_CODE_VARIANTS:\n            break",
     "        if False:\n            break"),

    # ---------------------------------------------------------- isolation (§60)
    ("F3a", "project filter fails open",
     "alirag/retrieve.py",
     "            hydrated = [h for h in hydrated if h[\"chunk_id\"] in allowed]",
     "            hydrated = [h for h in hydrated if h[\"chunk_id\"] in allowed] or hydrated"),
    ("F3b", "exact-ID leg unscoped",
     "alirag/sparse.py",
     "                if allowed_chunks is not None and cid not in allowed_chunks:",
     "                if False:"),
    ("F3c", "re-inference leaves FTS project labels stale",
     "alirag/inventory.py",
     "    fts_synced = _sync_sparse_projects(cfg, mf)", "    fts_synced = 0"),
    ("N4-2", "unattributed evidence merged with a named project",
     "alirag/verify.py",
     "        if mixed and not cross_project and (sensitive or projects):",
     "        if False:"),
    # F5-5 split into one mutation per mechanism (round-6 reviewer).
    ("F5-5a", "escalation ignores monetary evidence",
     "alirag/verify.py",
     "        if (MONEY_RE.search(text) or DATE_RE.search(text)",
     "        if (False or DATE_RE.search(text)"),
    ("F5-5b", "escalation ignores dates in evidence",
     "alirag/verify.py",
     "        if (MONEY_RE.search(text) or DATE_RE.search(text)\n"
     "                or QUANTITY_RE.search(text)):",
     "        if (MONEY_RE.search(text) or False\n"
     "                or QUANTITY_RE.search(text)):"),
    ("F5-5c", "escalation ignores quantities in evidence",
     "alirag/verify.py",
     "                or QUANTITY_RE.search(text)):",
     "                or False):"),
    ("F5-5d", "the query-side sensitive-intent trigger is dead",
     "alirag/verify.py",
     "        sensitive = _sensitive_evidence(kept) or bool(\n"
     "            query and SENSITIVE_INTENT.search(query))",
     "        sensitive = _sensitive_evidence(kept)"),

    # ---------------------------------------------------- round-6 findings
    ("R6-1", "safety walk skips excluded dirs instead of classifying them",
     "alirag/safety.py",
     "                elif self._is_excluded_dir(Path(p)):",
     "                elif False:"),
    ("R6-1b", "an excluded dir holding DOCUMENTS does not fail",
     "alirag/safety.py",
     "                      or excl_doc_mod or excl_doc_del)", ")"),
    ("R6-2", "a figure/date question is answerable from evidence with neither",
     "alirag/verify.py",
     "    if query and QUANTITATIVE_INTENT.search(query) and not _sensitive_evidence(kept):",
     "    if False:"),
    ("R6-2b", "the shape check widens to non-quantitative questions",
     "alirag/verify.py",
     "    if query and QUANTITATIVE_INTENT.search(query) and not _sensitive_evidence(kept):",
     "    if query and SENSITIVE_INTENT.search(query) and not _sensitive_evidence(kept):"),
    ("R6-3", "bare numbers indexed as document identifiers",
     "alirag/sparse.py",
     "                if not (any(c.isalpha() for c in window)\n"
     "                        and any(c.isdigit() for c in window)):\n"
     "                    continue\n", ""),
    ("R6-3b", "query-side codes not expanded to variants",
     "alirag/verify.py",
     "    for c in harvest_ids(query, limit=8):\n"
     "        if is_document_code(c):\n"
     "            out |= code_variants(c, limit=8)\n", ""),

    # ------------------------------------- mutations contributed by the reviewer
    ("RV-6", "MAX_CODE_VARIANTS raised to infinity",
     "alirag/sparse.py", "MAX_CODE_VARIANTS = 400", "MAX_CODE_VARIANTS = 10**9"),
    ("RV-8", "second-pass re-verify loses known_projects",
     "alirag/answer.py",
     "                                 doc_freq=df, total_docs=ndocs,\n"
     "                                 known_projects=self._projects_cache)",
     "                                 doc_freq=df, total_docs=ndocs)"),
    ("RV-9", "excluded-dir match becomes leaf-only",
     "alirag/safety.py",
     "        return any(part.lower() in low for part in path.parts)",
     "        return path.name.lower() in low"),
    ("RV-10", "dense leg loses pre-fusion project scoping",
     "alirag/retrieve.py",
     "                r = self.dense.search(qvec, k=policy.dense_k,\n"
     "                                      allowed_chunks=allowed)",
     "                r = self.dense.search(qvec, k=policy.dense_k)"),
    ("RV-13", "exact leg stops preferring filename hits over body mentions",
     "alirag/sparse.py",
     "                hits[cid] = max(hits.get(cid, 0.0), 2.0 if in_fn else 1.0)",
     "                hits[cid] = max(hits.get(cid, 0.0), 1.0)"),
    ("R3-1", "cross-project consent inferred from ordinary questions",
     "alirag/router.py",
     "    r\"\\bcompare (all|the|these|both|multiple) projects\\b\",",
     "    r\"\\bcompare\\b.*\\bprojects?\\b\", r\"\\bany project\\b\","),
    ("N4-4b", "graph leg unscoped before fusion",
     "alirag/retrieve.py",
     "            if allowed is not None:\n"
     "                graph_ids = [c for c in graph_ids if c in allowed]\n", ""),
    ("EXACT-LEG", "exact-ID retrieval leg disabled entirely",
     "alirag/retrieve.py",
     "                      if exact_ids and policy.sparse_k > 0 else [])",
     "                      if False else [])"),

    # ---------------------------------------------------------- data safety
    ("N4-6a", "over-broad volatile patterns accepted",
     "alirag/safety.py",
     "            _reject_overbroad_pattern(pat, self.excluded_dirs)",
     "            pass"),
    ("F5-4", "volatile patterns accepted when no excluded dirs are configured",
     "alirag/safety.py",
     "    if not allowed:\n        raise VolatilePatternRejected(",
     "    if False:\n        raise VolatilePatternRejected("),
    ("F5-3", "safety walk ignores excluded directories",
     "alirag/safety.py",
     "                if self._is_excluded_dir(dp):",
     "                if False:"),
    ("N4-6c", "audit certifies a run carried by an allowlist",
     "alirag/reviewer.py",
     "             safety.get(\"verdict\", \"PASS\" if safety.get(\"pass\") else \"FAIL\") == \"PASS\"",
     "             bool(safety.get(\"pass\"))"),
    ("R3-7b", "clean pass indistinguishable from an excused one",
     "alirag/safety.py",
     "        verdict = (\"PASS\" if passed and not excused else",
     "        verdict = (\"PASS\" if passed else"),
    ("HASH", "snapshot hashes not recorded",
     "alirag/safety.py",
     "            \"hashed\": any(r.get(\"h\") for r in before.values()),",
     "            \"hashed\": True,"),

    # ---------------------------------------------------------- provenance (§4)
    ("N4-9", "project_source missing from citations",
     "alirag/answer.py",
     "            \"project_source\": h.get(\"project_source\"),\n", "\n"),
    ("F5-7", "re-inference forges project_source",
     "alirag/inventory.py",
     "                \"UPDATE files SET project=?, project_source=?, document_type=?, \"\n"
     "                \"discipline=?, revision=? WHERE file_id=?\",\n"
     "                (meta[\"project\"], meta[\"project_source\"], meta[\"document_type\"],",
     "                \"UPDATE files SET project=?, project_source=project_source, \"\n"
     "                \"document_type=?, \"\n"
     "                \"discipline=?, revision=? WHERE file_id=?\",\n"
     "                (meta[\"project\"], meta[\"document_type\"],"),
    ("PCT", "project_unknown_pct hardcoded",
     "alirag/manifest.py",
     "        s[\"project_unknown_pct\"] = (round(100.0 * unknown / s[\"files_total\"], 1)",
     "        s[\"project_unknown_pct\"] = (0.0 and round(100.0 * unknown / s[\"files_total\"], 1)"),

    # ---------------------------------------------------------- honesty gates
    ("N4-10", "unattributed sources scored as correct",
     "alirag/bench.py",
     "        attributed = [s for s in top3\n"
     "                      if s.get(\"project\") not in (\"UNKNOWN\", None, \"\")]",
     "        attributed = list(top3)"),
    ("WPR", "wrong-project gate accepts any rate",
     "alirag/reviewer.py", "and wpr < 0.2)", "and wpr < 1.1)"),
    ("N5", "duplicate benchmark questions accepted",
     "alirag/bench.py",
     "        return \" \".join(re.sub(r\"[^\\w\\s]\", \" \", q.lower()).split())",
     "        return \" \".join(q.lower().split())"),
    ("N6", "quality gates need no sample",
     "alirag/reviewer.py",
     "        return bool(rep) and rep.get(\"questions\", 0) >= MIN_BENCH_QUESTIONS",
     "        return True"),
    ("N7", "audit picks evidence by filename",
     "alirag/reviewer.py", "    return sorted(hits, key=key)[-1]",
     "    return sorted(hits)[-1]"),
    ("R3-8", "project label sync fails silently",
     "alirag/inventory.py",
     "        raise SyncError(\n            f\"project label sync failed after {n} row(s): {e}. The sparse \"",
     "        return n\n        raise SyncError(\n"
     "            f\"project label sync failed after {n} row(s): {e}. The sparse \""),
    ("R3-9", "degraded lexical leg invisible",
     "alirag/sparse.py",
     "            if trace is not None:\n                trace.set(\"sparse_error\", str(e)[:200])",
     "            pass"),
    ("R3-10", "tiebreak uses its own tokenizer",
     "alirag/retrieve.py",
     "    from .terms import content_terms\n    qterms = content_terms(query)",
     "    import re as _re\n"
     "    qterms = {w.lower() for w in _re.findall(r'\\w+', query) if len(w) > 2}"),
]


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)[-4000:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append",
                    help="run only these mutation ids (repeatable)")
    ap.add_argument("--json", help="write results as JSON to this path")
    args = ap.parse_args()

    selected = [m for m in MUTATIONS
                if not args.only or m[0] in set(args.only)]
    if not selected:
        print(f"no mutations matched {args.only}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="revert_matrix_") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(REPO / "v3", work / "v3",
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

        code, out = run([sys.executable, "-m", "pytest", "v3/tests", "-q"], work)
        if code != 0:
            print("BASELINE IS NOT GREEN — fix that before reading anything below")
            print(out[-2000:])
            return 2
        baseline = out.strip().splitlines()[-1]
        print(f"baseline: {baseline}\n")

        results, unguarded = [], []
        for mid, desc, relpath, old, new in selected:
            target = work / "v3" / relpath
            original = target.read_text(encoding="utf-8")
            if old not in original:
                print(f"  !! {mid:<10} FRAGMENT NOT FOUND in {relpath} "
                      "— the fix was rewritten; update this mutation")
                results.append({"id": mid, "status": "STALE", "desc": desc})
                continue
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            try:
                code, out = run([sys.executable, "-m", "pytest", "v3/tests", "-q",
                                 "-x", "--no-header", "-p", "no:cacheprovider"], work)
            finally:
                target.write_text(original, encoding="utf-8")

            tail = out.strip().splitlines()[-1] if out.strip() else ""
            red = code != 0
            status = "RED (guarded)" if red else "GREEN (UNGUARDED)"
            if not red:
                unguarded.append(mid)
            print(f"  {'ok ' if red else 'XX '} {mid:<10} {status:<18} {desc}")
            results.append({"id": mid, "status": "RED" if red else "GREEN",
                            "desc": desc, "tail": tail})

    print()
    if unguarded:
        print(f"UNGUARDED FIXES ({len(unguarded)}): {', '.join(unguarded)}")
        print("Each of these can be broken without any test noticing.")
    else:
        print(f"All {len(results)} mutations were caught by the suite.")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")

    return 1 if unguarded else 0


if __name__ == "__main__":
    raise SystemExit(main())
