"""The database connection, and a session that remembers the SQL it ran so the page can show it."""
import inspect
import threading
import time

import pymysql

from . import config

_local = threading.local()


def connect():
    con = pymysql.connect(**config.DB, charset="utf8mb4", autocommit=True)
    cur = con.cursor()
    cur.execute("SET time_zone = '+00:00'")                  # Wikipedia's times are UTC
    try:
        cur.execute(f"SET mhnsw_ef_search = {config.EF_SEARCH}")
    except pymysql.MySQLError:
        pass                                                 # a MariaDB without vectors: the graph still works
    return con


def small_memory(con=None):
    """A note for the person running this when MariaDB has the small memory settings of a fresh
    install, or None. Measured with them: loading takes twice as long, a search most of a second."""
    try:
        cur = (con or connect()).cursor()
        cur.execute("SELECT @@innodb_buffer_pool_size, @@mhnsw_max_cache_size")
        pool, cache = (int(n) >> 20 for n in cur.fetchone())
    except pymysql.MySQLError:
        return None
    if pool >= 512 and cache >= 128:
        return None
    return (f"MariaDB runs with small memory settings (buffer pool {pool} MB, vector index cache {cache} MB).\n"
            "Everything works, but loading takes about twice as long and a search most of a second.\n"
            "With a Homebrew MariaDB:  ./setup.sh --local --tune   (writes a two-line config file, restarts MariaDB)\n"
            "Otherwise put these two lines in the [mariadbd] section of its configuration and restart it:\n"
            "    innodb_buffer_pool_size = 1G\n"
            "    mhnsw_max_cache_size = 512M")


def _connection():
    con = getattr(_local, "con", None)
    if con is None:
        con = _local.con = connect()
    else:
        try:
            con.ping(reconnect=False)
        except pymysql.MySQLError:
            con = _local.con = connect()
    return con


def show(sql, params):
    """The statement with its values filled in, for people to read. Not for execution."""
    def lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, (bytes, bytearray)):
            return f"<vector of {len(v) // 4} floats>"
        if isinstance(v, (int, float)):
            return str(v)
        v = str(v)
        return "'" + (v if len(v) <= 80 else v[:77] + "…").replace("'", "''") + "'"
    sql = inspect.cleandoc(sql)                 # the statements are indented in the source; not on the page
    parts = sql.split("%s")
    if not params or len(parts) - 1 != len(params):
        return sql
    out = parts[0]
    for value, rest in zip(params, parts[1:]):
        out += lit(value) + rest
    return out.replace("%%", "%")


class Session:
    """One request's view of the database.

        with Session() as s:
            s.asof("2012-06-01")            # every query below sees the database as of that day
            rows = s.q("SELECT target FROM link WHERE src = %s", (1256,))
            s.log                           # [{"sql": ..., "ms": ..., "rows": ...}] for the SQL drawer
    """

    def __init__(self):
        self.con = _connection()
        self.cur = self.con.cursor(pymysql.cursors.DictCursor)
        self.log = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.cur.execute("SET system_versioning_asof = DEFAULT")   # never leak time travel into the next request
        except pymysql.MySQLError:
            _local.con = None
        self.cur.close()

    def q(self, sql, params=(), note=None, quiet=False):
        params = tuple(params)
        t0 = time.perf_counter()
        self.cur.execute(sql, params or None)          # no values: the driver leaves % alone
        rows = self.cur.fetchall()
        if not quiet:
            self.log.append({"sql": show(sql, params), "ms": round((time.perf_counter() - t0) * 1000, 1),
                             "rows": len(rows), "note": note})
        return list(rows)

    def one(self, sql, params=(), **kw):
        rows = self.q(sql, params, **kw)
        return rows[0] if rows else None

    def asof(self, when):
        """when: 'YYYY-MM-DD[ HH:MM:SS]' (UTC), or None for now."""
        if when:
            when = str(when).replace("T", " ")
            when += {10: " 00:00:00", 16: ":00"}.get(len(when), "")
            self.q("SET system_versioning_asof = %s", (when,))
        else:
            self.q("SET system_versioning_asof = DEFAULT", quiet=True)
