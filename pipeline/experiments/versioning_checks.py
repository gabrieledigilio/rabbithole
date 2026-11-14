"""Small checks of how system versioning and vectors behave together on the MariaDB you run.

    python pipeline/experiments/versioning_checks.py

Each line is something Rabbit Hole relies on, or had to work around. The script reports what the
server does; it does not assume an answer, so it is also a way to see what changed in a newer
MariaDB. It makes a few tables whose names start with x_check_ and drops them at the end.
"""
import os
import struct
import sys

import pymysql

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from rabbithole import db  # noqa: E402

con = db.connect()
cur = con.cursor()
THEN, NOW = 10, 20                  # the value a row had from 2010 to 2020, and has had since
ASOF = "2015-01-01 00:00:00"


def run(sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def report(what, result):
    print(f"  {what:<74} {result}", flush=True)


def attempt(what, sql, params=None, ok="yes"):
    try:
        run(sql, params)
        report(what, ok)
        return True
    except pymysql.MySQLError as e:
        report(what, f"no: {e.args[1] if len(e.args) > 1 else e}")
        return False


def vec(*numbers):
    return struct.pack(f"<{len(numbers)}f", *numbers)


def cleanup():
    run("DROP VIEW IF EXISTS x_check_view")
    run("DROP TABLE IF EXISTS x_check_t, x_check_u, x_check_out, x_check_ctas, x_check_vec, x_check_part")


cleanup()
print(f"MariaDB {run('SELECT VERSION()')[0][0]}\n")

# ---------------------------------------------------------------- vectors on a versioned table
print("Vectors and system versioning in one table")
attempt("a VECTOR INDEX on a system-versioned table can be created", """
    CREATE TABLE x_check_vec (
      id INT NOT NULL PRIMARY KEY, emb VECTOR(4) NOT NULL,
      row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
      row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
      PERIOD FOR SYSTEM_TIME (row_start, row_end),
      VECTOR INDEX (emb) M=8 DISTANCE=cosine) WITH SYSTEM VERSIONING""")
run("SET system_versioning_insert_history = 1")
try:
    # 40 rows; each pointed one way from 2010 to 2020 and points another way since
    cur.executemany("INSERT INTO x_check_vec (id, emb, row_start, row_end) VALUES (%s, %s, '2010-01-01', '2020-01-01')",
                    [(i, vec(1, i / 40, 0, 0)) for i in range(1, 41)])
    cur.executemany("INSERT INTO x_check_vec (id, emb, row_start) VALUES (%s, %s, '2020-01-01')",
                    [(i, vec(0, 0, 1, i / 40)) for i in range(1, 41)])
    report("history rows with vectors can be written with their own dates", "yes (system_versioning_insert_history)")
except pymysql.MySQLError as e:
    report("history rows with vectors can be written with their own dates", f"no: {e.args[1]}")
run("SET system_versioning_insert_history = 0")
probe = vec(1, 0.5, 0, 0)           # close to the 2010-2020 vectors, far from today's
then = run(f"SELECT id FROM x_check_vec FOR SYSTEM_TIME AS OF TIMESTAMP '{ASOF}' ORDER BY VEC_DISTANCE_COSINE(emb, %s) LIMIT 3", (probe,))
report("the nearest rows AS OF 2015 are the ones that were nearest in 2015", "yes" if {r[0] for r in then} == {19, 20, 21} else f"no: {then}")
plan = run(f"EXPLAIN SELECT id FROM x_check_vec FOR SYSTEM_TIME AS OF TIMESTAMP '{ASOF}' ORDER BY VEC_DISTANCE_COSINE(emb, %s) LIMIT 3", (probe,))
report("... and that search uses the vector index", "yes" if plan[0][5] == "emb" else f"no: key = {plan[0][5]}")
plan = run("EXPLAIN SELECT id FROM x_check_vec ORDER BY VEC_DISTANCE_COSINE(emb, (SELECT emb FROM x_check_vec WHERE id = 7)) LIMIT 3")
report("a vector handed over by a scalar subquery still uses the index", "yes" if plan[0][5] == "emb" else f"no: key = {plan[0][5]}")
run("UPDATE x_check_vec SET emb = %s WHERE id = 7", (vec(0, 1, 0, 0),))
report("an UPDATE keeps the old vector as a history row", f"{run('SELECT COUNT(*) FROM x_check_vec FOR SYSTEM_TIME ALL WHERE id = 7')[0][0]} versions of row 7")
attempt("a versioned table with a VECTOR INDEX can be PARTITIONed BY SYSTEM_TIME", """
    CREATE TABLE x_check_part (id INT PRIMARY KEY, emb VECTOR(4) NOT NULL, VECTOR INDEX (emb)) WITH SYSTEM VERSIONING
    PARTITION BY SYSTEM_TIME (PARTITION p_hist HISTORY, PARTITION p_cur CURRENT)""")

# ---------------------------------------------------------------- where SET system_versioning_asof reaches
print(f"\nSET system_versioning_asof = '{ASOF}': which statements then read the past?")
for table in ("x_check_t", "x_check_u"):
    run(f"""CREATE TABLE {table} (
      id INT NOT NULL PRIMARY KEY, v INT NOT NULL,
      row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
      row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
      PERIOD FOR SYSTEM_TIME (row_start, row_end)) WITH SYSTEM VERSIONING""")
    run("SET system_versioning_insert_history = 1")
    run(f"INSERT INTO {table} (id, v, row_start, row_end) VALUES (1, {THEN}, '2010-01-01', '2020-01-01')")
    run(f"INSERT INTO {table} (id, v, row_start) VALUES (1, {NOW}, '2020-01-01')")
    run("SET system_versioning_insert_history = 0")
run("CREATE VIEW x_check_view AS SELECT v FROM x_check_t WHERE id = 1")
run("CREATE TABLE x_check_out (v INT)")


def reads(what, sql, after=None):
    """Run sql under the setting; `after` reads back what a writing statement stored."""
    try:
        run("SET system_versioning_asof = %s", (ASOF,))
        rows = run(sql)
        if after:
            run("SET system_versioning_asof = DEFAULT")
            rows = run(after)
        seen = {r[0] for r in rows}
        report(what, "the past" if seen == {THEN} else "TODAY: the setting is ignored" if seen == {NOW} else f"mixed: {sorted(seen)}")
    except pymysql.MySQLError as e:
        report(what, f"error: {e.args[1] if len(e.args) > 1 else e}")
    finally:
        run("SET system_versioning_asof = DEFAULT")


reads("SELECT", "SELECT v FROM x_check_t WHERE id = 1")
reads("a join of two versioned tables", "SELECT t.v + u.v - %d FROM x_check_t t JOIN x_check_u u USING (id)" % THEN)
reads("a subquery", "SELECT (SELECT v FROM x_check_t WHERE id = 1)")
reads("a derived table", "SELECT v FROM (SELECT v FROM x_check_t) d")
reads("a common table expression", "WITH c AS (SELECT v FROM x_check_t) SELECT v FROM c")
reads("a recursive CTE (both its halves)", """
    WITH RECURSIVE r AS (SELECT v, 1 AS n FROM x_check_t WHERE id = 1
                         UNION ALL SELECT u.v, r.n + 1 FROM r JOIN x_check_u u ON u.id = 1 WHERE r.n < 3)
    SELECT v FROM r""")
reads("a view", "SELECT v FROM x_check_view")
# A prepared statement reads the time that was set when it was prepared, whatever is set when it runs.
run("PREPARE x_check_before FROM 'SELECT v FROM x_check_t WHERE id = 1'")
reads("a statement prepared before the setting, executed under it", "EXECUTE x_check_before")
run("SET system_versioning_asof = %s", (ASOF,))
run("PREPARE x_check_under FROM 'SELECT v FROM x_check_t WHERE id = 1'")
reads("a statement prepared under the setting, executed under it", "EXECUTE x_check_under")
seen = {r[0] for r in run("EXECUTE x_check_under")}            # the setting is back to DEFAULT here
report("... and executed again after the setting is back to DEFAULT", "today" if seen == {NOW} else "STILL THE PAST" if seen == {THEN} else sorted(seen))
run("DEALLOCATE PREPARE x_check_before")
run("DEALLOCATE PREPARE x_check_under")
reads("INSERT ... SELECT", "INSERT INTO x_check_out SELECT v FROM x_check_t WHERE id = 1", after="SELECT v FROM x_check_out")
reads("CREATE TABLE ... AS SELECT", "CREATE TABLE x_check_ctas AS SELECT v FROM x_check_t WHERE id = 1", after="SELECT v FROM x_check_ctas")
run("DELETE FROM x_check_out")
run("SET system_versioning_asof = DEFAULT")
try:
    run(f"INSERT INTO x_check_out SELECT v FROM x_check_t FOR SYSTEM_TIME AS OF TIMESTAMP '{ASOF}' WHERE id = 1")
    seen = {r[0] for r in run("SELECT v FROM x_check_out")}
    report("INSERT ... SELECT with FOR SYSTEM_TIME AS OF written in the SELECT", "the past" if seen == {THEN} else f"not the past: {sorted(seen)}")
except pymysql.MySQLError as e:
    report("INSERT ... SELECT with FOR SYSTEM_TIME AS OF written in the SELECT", f"error: {e.args[1]}")

cleanup()
