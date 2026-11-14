"""Time the questions the pages ask of MariaDB.

    python pipeline/measure.py            # 7 runs of each, the median

The time is the SQL only, as the app's own log counts it: sending the statement, running it and
fetching the rows. No model is involved. The hybrid search needs a question as a vector; the
vector of an article stands in for one (the statement and its cost are the same).
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import queries as Q  # noqa: E402
from rabbithole.db import Session  # noqa: E402

RUNS = 7


def page_id(title):
    with Session() as s:
        row = s.one("SELECT page_id FROM page WHERE title = %s", (title,), quiet=True)
    if not row:
        sys.exit(f"No article called {title}. Has pipeline/load.py run?")
    return row["page_id"]


talvisota, isoviha, war, kekkonen = (page_id(t) for t in ("Talvisota", "Isoviha", "Suomen sisällissota", "Urho Kekkonen"))
with Session() as s:
    question = s.one("SELECT embedding FROM article_vector WHERE lang = 'fi' AND page_id = %s", (talvisota,), quiet=True)["embedding"]
    version = s.one("SELECT VERSION() AS v", quiet=True)["v"]

CASES = [
    ("An article and its timeline (Talvisota)", lambda s: Q.page(s, talvisota)),
    ("Its connections through time: 150 of them, every period (Talvisota)", lambda s: Q.graph(s, talvisota)),
    ("The same for the most linked-to article (Suomen sisällissota)", lambda s: Q.graph(s, war)),
    ("The article as of 1 June 2012: opening, counts, links since", lambda s: Q.asof(s, talvisota, "2012-06-01 00:00:00")),
    ("Nearest in meaning x linked today x linked in 2012 x ever: one statement", lambda s: Q.neighbours(s, talvisota, "2012-06-01 00:00:00")),
    ("The life of one link (Isoviha > Kansanmurha)", lambda s: Q.edge(s, isoviha, "Kansanmurha")),
    ("Every opening an article has had (Talvisota)", lambda s: Q.lead_history(s, talvisota)),
    ("Shortest way between two articles today (Isoviha > Urho Kekkonen)", lambda s: Q.path(s, isoviha, kekkonen)),
    ("... and as of 2008", lambda s: Q.path(s, isoviha, kekkonen, "2008-01-01 00:00:00")),
    ("The whole graph as of the end of 2010: size and the 10 most linked-to", lambda s: Q.hubs(s, 2010)),
    ("Hybrid search: vector + full text, fused, one statement", lambda s: Q.search(s, "Mikä sota käytiin talvella 1939–1940?", question)),
    ("Keyword search alone", lambda s: Q.search(s, "Mikä sota käytiin talvella 1939–1940?")),
    ("Follow the links of five results", lambda s: Q.follow_links(s, [talvisota, isoviha, war, kekkonen, page_id("Jatkosota")])),
    ("Contested connections (the Disputes page)", lambda s: Q.disputes(s)),
    ("Linked before written (a Gaps page)", lambda s: Q.gaps(s, "waited")),
]

print(f"MariaDB {version}, {RUNS} runs each\n")
print(f"{'question':<74} {'statements':>10} {'median':>9} {'slowest':>9}")
for label, ask in CASES:
    took, statements = [], 0
    for _ in range(RUNS):
        with Session() as s:
            ask(s)
            took.append(sum(entry["ms"] for entry in s.log))
            statements = len(s.log)
    print(f"{label:<74} {statements:>10} {statistics.median(took):>6.1f} ms {max(took):>6.1f} ms")
