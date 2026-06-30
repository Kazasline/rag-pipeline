# demo-search.py "your question" — the "section-by-section matching" brain: turn a question into
# an embedding and find the most similar document SECTIONS (chunks) across the whole corpus.
# Run:  .\.venv\Scripts\python.exe demo-search.py "tree planting plan level 9"
import sys
sys.path.insert(0, r"P:\RAG Database\pipeline")
import query

q = " ".join(sys.argv[1:]).strip() or "tree planting plan level 9"
print(f"\n  SEMANTIC SECTION MATCH  (bge-m3 embedding -> cosine search over the index)")
print(f"  QUERY: {q}")
print(f"  {'='*62}")
res = query.search(q, k=6)
print(f"  Top {len(res)} matching document sections:\n")
for i, r in enumerate(res, 1):
    snip = (r.get("text") or "").strip().replace("\n", " ")[:170]
    pg = f"p{r['page']}" if r.get("page") else "-"
    print(f"  {i}. score {r['score']:.3f}   {r['name'][:46]:46}  {pg}")
    print(f"        ...{snip}...\n")
