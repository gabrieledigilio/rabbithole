"""Experiment: what does a growing history cost a vector search on a system-versioned table?

    python pipeline/experiments/vector_history_depth.py

Takes the vectors of 5,000 Finnish articles and builds three versioned tables with a vector
index: with 1, 3 and 9 versions of every row. Every version is the real vector plus a little
noise of its own, so the versions of one row are near-duplicates of each other, as successive
versions of an article are. Then it asks each table for the 10 nearest *current* rows (no time
clause at all) and for the 10 nearest as of the oldest period, and compares with the exact
answer computed outside the database.

vector_time_travel.py asks a different question (versions that differ in quality, one depth).
Needs the article_vector table (pipeline/load.py). About three minutes.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from rabbithole import db  # noqa: E402

ROWS, NOISE, EF = 5000, 0.3, (80, 400)
con = db.connect()
cur = con.cursor()
rng = np.random.default_rng(11)

cur.execute("SELECT page_id, embedding FROM article_vector WHERE lang = 'fi' ORDER BY page_id LIMIT %s", (ROWS,))
rows = cur.fetchall()
ids = np.array([r[0] for r in rows])
real = np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), -1)
dim = real.shape[1]


def unit(m):
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def version():
    return unit(unit(real) + NOISE * rng.standard_normal(real.shape, dtype=np.float32) / np.sqrt(dim)).astype("<f4")


def questions(matrix, probes):
    return [unit(matrix[p:p + 1] + 0.05 * rng.standard_normal((1, dim), dtype=np.float32) / np.sqrt(dim))[0].astype("<f4") for p in probes]


def measure(table, clause, matrix, asked, ef):
    cur.execute(f"SET mhnsw_ef_search = {ef}")
    found, took = 0, []
    for q in asked:
        exact = set(ids[np.argsort(-(matrix @ q))[:10]].tolist())
        t1 = time.perf_counter()
        cur.execute(f"SELECT page_id FROM {table} {clause} ORDER BY VEC_DISTANCE_COSINE(embedding, %s) LIMIT 10", (q.tobytes(),))
        got = {r[0] for r in cur.fetchall()}
        took.append((time.perf_counter() - t1) * 1000)
        found += len(exact & got)
    return f"{found / (10 * len(asked)):4.0%} {sorted(took)[len(took) // 2]:5.1f} ms"


cur.execute("SELECT VERSION()")
print(f"MariaDB {cur.fetchone()[0]}: {len(ids):,} rows, each with 1, 3 or 9 near-duplicate versions; recall@10 and median time\n")
print(f"{'versions per row':<17} {'rows in index':>13}   " + "   ".join(f"{'current rows, ef ' + str(ef):<22}" for ef in EF)
      + "   " + "   ".join(f"{'oldest period, ef ' + str(ef):<22}" for ef in EF))
probes = rng.choice(len(ids), 60, replace=False)
for depth in (1, 3, 9):
    table = f"x_depth_{depth}"
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"""CREATE TABLE {table} (
      page_id   INT UNSIGNED NOT NULL,
      embedding VECTOR({dim}) NOT NULL,
      row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
      row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
      PERIOD FOR SYSTEM_TIME (row_start, row_end),
      PRIMARY KEY (page_id),
      VECTOR INDEX (embedding) M=16 DISTANCE=cosine) WITH SYSTEM VERSIONING""")
    cur.execute("SET system_versioning_insert_history = 1")
    oldest = None
    for n in range(depth - 1):                              # history: one version per year from 2010
        matrix = version()
        oldest = matrix if oldest is None else oldest
        for i in range(0, len(ids), 200):
            cur.executemany(f"INSERT INTO {table} (page_id, embedding, row_start, row_end) VALUES (%s, %s, %s, %s)",
                            [(int(p), v.tobytes(), f"{2010 + n}-01-01", f"{2011 + n}-01-01") for p, v in zip(ids[i:i + 200], matrix[i:i + 200])])
    current = version()
    for i in range(0, len(ids), 200):
        cur.executemany(f"INSERT INTO {table} (page_id, embedding, row_start) VALUES (%s, %s, %s)",
                        [(int(p), v.tobytes(), f"{2009 + depth}-01-01") for p, v in zip(ids[i:i + 200], current[i:i + 200])])
    con.commit()
    cur.execute("SET system_versioning_insert_history = 0")
    now = [measure(table, "", current, questions(current, probes), ef) for ef in EF]
    then = ([measure(table, "FOR SYSTEM_TIME AS OF TIMESTAMP '2010-06-01 00:00:00'", oldest, questions(oldest, probes), ef) for ef in EF]
            if oldest is not None else ["(no history)"] * len(EF))
    print(f"{depth:<17} {depth * len(ids):>13,}   " + "   ".join(f"{x:<22}" for x in now + then), flush=True)
    cur.execute(f"DROP TABLE {table}")
