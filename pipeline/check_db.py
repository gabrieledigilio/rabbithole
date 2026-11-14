"""Can we reach MariaDB, and is it one Rabbit Hole can use?

    python pipeline/check_db.py        # exit 0: yes · 2: cannot connect · 3: too old

setup.sh runs it before loading. It also says so when the server runs with the small memory
settings of a fresh local install, because loading and searching are then slower. (The buffer
pool cannot be raised on a running server past the size it started with; it takes a restart.)
"""
import os
import re
import sys

import pymysql

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import config, db  # noqa: E402

where = f"{config.DB['host']}:{config.DB['port']}"
try:
    con = db.connect()
except pymysql.MySQLError as e:
    print(f"Cannot connect to MariaDB at {where} as '{config.DB['user']}' (database '{config.DB['database']}'): {e.args[-1]}")
    sys.exit(2)
cur = con.cursor()
cur.execute("SELECT VERSION()")
version = cur.fetchone()[0]
number = tuple(int(n) for n in re.findall(r"\d+", version)[:2])
if "mariadb" not in version.lower() or number < (11, 8):
    print(f"The server at {where} is {version}. Rabbit Hole needs MariaDB 11.8 or later "
          "(vector index, and history rows loaded with their own dates).")
    sys.exit(3)
print(f"     MariaDB {version} at {where}")
note = db.small_memory(con)
if note:
    print("     " + note.replace("\n", "\n     "))
