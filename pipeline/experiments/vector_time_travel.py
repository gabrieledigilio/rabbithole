"""Experiment: how well does vector search work on a system-versioned table, as of a past date?

    python pipeline/experiments/vector_time_travel.py
        --keep    leave the two x_ tables in the database
        --reuse   measure on the tables a run with --keep left (the data is the same every run: fixed seed)

Builds a versioned table with a VECTOR INDEX and three versions of every Finnish article's vector:
the real bge-m3 vector as "today", and two noisier copies standing in for 2005-2012 and 2012-2020
(we have no embeddings of old text; the copies only need to be near their own article, as old
text is). They are written with their dates through system_versioning_insert_history.

Then it asks for the 10 nearest rows as of 2008, 2016 and today, at several values of
mhnsw_ef_search, and compares with the exact answer computed outside the database. A control
table holds the 2008 vectors alone, without versioning: the difference between the two is what it
costs to share one index with the other versions.

Needs the article_vector table (pipeline/load.py). Takes about five minutes, most of it building.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from rabbithole import db  # noqa: E402

Y2008, Y2016, TODAY = "2008-01-01 00:00:00", "2016-01-01 00:00:00", None
con = db.connect()
cur = con.cursor()
rng = np.random.default_rng(7)

cur.execute("SELECT page_id, embedding FROM article_vector WHERE lang = 'fi' ORDER BY page_id")
rows = cur.fetchall()
ids = np.array([r[0] for r in rows])
real = np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), -1)
dim = real.shape[1]


def unit(m):
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def blurred(amount):
    return unit(unit(real) + amount * rng.standard_normal(real.shape, dtype=np.float32) / np.sqrt(dim)).astype("<f4")


versions = {Y2008: blurred(0.6), Y2016: blurred(0.3), TODAY: unit(real).astype("<f4")}


def batches(size=200):
    for i in range(0, len(ids), size):
        yield slice(i, i + size)


def build():
    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS x_vector_history, x_vector_2008")
    cur.execute(f"""CREATE TABLE x_vector_history (
      page_id   INT UNSIGNED NOT NULL,
      embedding VECTOR({dim}) NOT NULL,
      row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
      row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
      PERIOD FOR SYSTEM_TIME (row_start, row_end),
      PRIMARY KEY (page_id),
      VECTOR INDEX (embedding) M=16 DISTANCE=cosine) WITH SYSTEM VERSIONING""")
    cur.execute("SET system_versioning_insert_history = 1")
    for part in batches():
        cur.executemany("INSERT INTO x_vector_history (page_id, embedding, row_start, row_end) VALUES (%s, %s, %s, %s)",
                        [(int(p), v.tobytes(), "2005-01-01", "2012-01-01") for p, v in zip(ids[part], versions[Y2008][part])] +
                        [(int(p), v.tobytes(), "2012-01-01", "2020-01-01") for p, v in zip(ids[part], versions[Y2016][part])])
        cur.executemany("INSERT INTO x_vector_history (page_id, embedding, row_start) VALUES (%s, %s, %s)",
                        [(int(p), v.tobytes(), "2020-01-01") for p, v in zip(ids[part], versions[TODAY][part])])
    con.commit()
    cur.execute("SET system_versioning_insert_history = 0")
    print(f"built x_vector_history in {time.time() - t0:.0f} s", flush=True)

    # The control: the 2008 vectors alone in an ordinary table with the same index.
    cur.execute(f"""CREATE TABLE x_vector_2008 (
      page_id   INT UNSIGNED NOT NULL PRIMARY KEY,
      embedding VECTOR({dim}) NOT NULL,
      VECTOR INDEX (embedding) M=16 DISTANCE=cosine)""")
    for part in batches():
        cur.executemany("INSERT INTO x_vector_2008 (page_id, embedding) VALUES (%s, %s)",
                        [(int(p), v.tobytes()) for p, v in zip(ids[part], versions[Y2008][part])])
    con.commit()


cur.execute("SHOW TABLES LIKE 'x_vector_%'")
if not ("--reuse" in sys.argv and len(cur.fetchall()) == 2):
    build()
cur.execute("SELECT VERSION()")
print(f"MariaDB {cur.fetchone()[0]}: x_vector_history has {3 * len(ids):,} rows with one vector index "
      f"({len(ids):,} current, {2 * len(ids):,} history)")

# 60 questions per date: a vector close to one article's vector of that date.
probes = rng.choice(len(ids), 60, replace=False)
questions = {when: [unit(matrix[p:p + 1] + 0.05 * rng.standard_normal((1, dim), dtype=np.float32) / np.sqrt(dim))[0].astype("<f4")
                    for p in probes] for when, matrix in versions.items()}


def measure(label, table, when, ef=80, hint=""):
    cur.execute(f"SET mhnsw_ef_search = {ef}")
    clause = (f"FOR SYSTEM_TIME AS OF TIMESTAMP '{when}'" if when and table == "x_vector_history" else "") + hint
    found = short = 0
    took = []
    for q in questions[when]:
        exact = set(ids[np.argsort(-(versions[when] @ q))[:10]].tolist())
        t1 = time.perf_counter()
        cur.execute(f"SELECT page_id FROM {table} {clause} ORDER BY VEC_DISTANCE_COSINE(embedding, %s) LIMIT 10", (q.tobytes(),))
        got = [r[0] for r in cur.fetchall()]
        took.append((time.perf_counter() - t1) * 1000)
        found += len(exact & set(got))
        short += 10 - len(got)
    print(f"  {label:<45} " + ("index not used" if hint else f"ef_search {ef:>4}") + f"   recall@10 {found / (10 * len(probes)):4.0%}   "
          f"rows missing: {short}   median {sorted(took)[len(took) // 2]:5.1f} ms", flush=True)


for ef in (80, 200, 400, 800):
    print()
    measure("versioned, today (newest of 3 versions)", "x_vector_history", TODAY, ef)
    measure("versioned, as of 2016 (middle version)", "x_vector_history", Y2016, ef)
    measure("versioned, as of 2008 (oldest version)", "x_vector_history", Y2008, ef)
    measure("control: the 2008 vectors alone, plain table", "x_vector_2008", Y2008, ef)

# The exact answer from the database itself: tell it to leave the index alone and compare every row.
print()
measure("versioned, as of 2008, every row compared", "x_vector_history", Y2008, hint=" IGNORE INDEX (embedding)")
measure("versioned, today, every row compared", "x_vector_history", TODAY, hint=" IGNORE INDEX (embedding)")

print()
cur.execute(f"EXPLAIN SELECT page_id FROM x_vector_history FOR SYSTEM_TIME AS OF TIMESTAMP '{Y2008}' "
            "ORDER BY VEC_DISTANCE_COSINE(embedding, %s) LIMIT 10", (versions[Y2008][0].tobytes(),))
print("  EXPLAIN, as of 2008 (type, key, rows):", [(r[3], r[5], r[8]) for r in cur.fetchall()])
if "--keep" not in sys.argv and "--reuse" not in sys.argv:
    cur.execute("DROP TABLE x_vector_history, x_vector_2008")
