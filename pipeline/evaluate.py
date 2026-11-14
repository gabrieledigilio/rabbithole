"""Measure how well the search finds the right article, and how fast.

    python pipeline/evaluate.py             # every mode this machine can run
    python pipeline/evaluate.py --misses    # also list the questions whose answer was not in the top 5

Reads data/s/test-questions.tsv (pipeline/download.py fetches it): the 37 test questions that come
with dataset S. Each is a question in Finnish, Swedish or English and the Finnish title of the
article that answers it. A hit is that topic in any of its languages (same wikidata_id).

  words     full-text search alone: what the app does when no embedding model runs
  meaning   vector search alone (needs bge-m3 in Ollama, to turn the question into a vector)
  both      one statement: vector search and full-text search, fused by rank. What the app does.

hit@1, hit@5, hit@10: the share of questions with the answer among the first 1, 5, 10 topics.
MRR: mean reciprocal rank (1 for first place, 1/2 for second, ... 0 if not in the first 10).
The last line is the app's extra step: besides the top 5 it hands the language model up to two
articles that those five link to together. "In the sources" counts the answer being anywhere there.
"""
import csv
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import config, llm, queries as Q  # noqa: E402
from rabbithole.db import Session  # noqa: E402

path = os.path.join(config.DATA, "s", "test-questions.tsv")
if not os.path.exists(path):
    sys.exit(f"{path} is missing. Run: python pipeline/download.py")
with open(path, encoding="utf-8") as f:
    questions = list(csv.DictReader(f, delimiter="\t"))

with Session() as s:
    for q in questions:
        row = s.one("SELECT page_id FROM article WHERE lang = 'fi' AND title = %s", (q["expected_fi_title"],), quiet=True)
        q["page_id"] = row["page_id"] if row else None
questions = [q for q in questions if q["page_id"]]

t0 = time.perf_counter()
vectors = llm.embed([q["question"] for q in questions])
embed_ms = (time.perf_counter() - t0) * 1000 / len(questions)
modes = ["words", "meaning", "both"] if vectors else ["words"]
if not vectors:
    print(f"No embedding model at {config.OLLAMA_URL} (ollama pull {config.EMBED_MODEL}): measuring keyword search only.\n")

print(f"{len(questions)} questions.  MariaDB: mhnsw_ef_search = {config.EF_SEARCH}")
print(f"{'mode':<9} | hit@1 | hit@5 | hit@10 |  MRR | SQL, median")
in_sources = 0
for mode in modes:
    ranks, took = [], []
    for i, q in enumerate(questions):
        with Session() as s:
            vector = vectors[i] if vectors else None
            t1 = time.perf_counter()
            topics = Q.search(s, q["question"], vector, limit=10, only=None if mode == "both" else mode)
            took.append((time.perf_counter() - t1) * 1000)
            rank = next((n for n, t in enumerate(topics, 1) if t["page_id"] == q["page_id"]), None)
            ranks.append(rank)
            if mode == modes[-1]:
                top5 = [t["page_id"] for t in topics if t["in_graph"]][:5]
                followed = [f["page_id"] for f in Q.follow_links(s, top5)]
                q["rank"], q["followed"] = rank, q["page_id"] in followed
                in_sources += q["page_id"] in top5 or q["followed"]

    def hit(k):
        return sum(1 for r in ranks if r and r <= k) / len(ranks)
    mrr = sum(1 / r for r in ranks if r) / len(ranks)
    print(f"{mode:<9} | {hit(1):5.0%} | {hit(5):5.0%} | {hit(10):6.0%} | {mrr:.2f} | {statistics.median(took):5.1f} ms")

print(f"\nIn the sources handed to the language model (top 5 of '{modes[-1]}' + what they link to together): "
      f"{in_sources / len(questions):.0%}, {sum(q['followed'] for q in questions)} of them found by following links.")
if vectors:
    print(f"Turning a question into a vector: {embed_ms:.0f} ms each on this machine ({config.EMBED_MODEL}, in one batch).")
if "--misses" in sys.argv:
    print(f"\nNot in the top 5 of '{modes[-1]}':")
    for q in questions:
        if not q["rank"] or q["rank"] > 5:
            print(f"  [{q['lang']}] {q['question']}  ->  {q['expected_fi_title']}  (rank {q['rank'] or 'none'}"
                  f"{', found by following links' if q['followed'] else ''})")
