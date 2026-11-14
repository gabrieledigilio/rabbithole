#!/bin/sh
# Set everything up in one go: Python packages, MariaDB, the two datasets, the link graph
# through time, and the load. About ten minutes, most of it downloading. Then:
#
#   .venv/bin/python -m rabbithole        and open http://localhost:8000
#
#   ./setup.sh                  # MariaDB in Docker (port 3309)
#   ./setup.sh --local          # no Docker: a MariaDB on this machine, 11.8 or later.
#                               # It makes the database and the user "rabbithole" if they are missing.
#                               # Settings other than the usual ones go in .env first (see .env.example).
#   ./setup.sh --local --tune   # the same, and gives a Homebrew MariaDB the memory this needs:
#                               # writes one small file in Homebrew's my.cnf.d and restarts MariaDB
#
# On a Mac, ./mac.sh does all of it, MariaDB included, and starts the app.
# Safe to run again: downloads resume, loaded tables are skipped.
set -e
cd "$(dirname "$0")"

LOCAL=""; TUNE=""
for arg in "$@"; do
  case "$arg" in
    --local) LOCAL=1 ;;
    --tune)  LOCAL=1; TUNE=1 ;;
    *) echo "Unknown option: $arg   (see the top of setup.sh)"; exit 1 ;;
  esac
done

need() { command -v "$1" >/dev/null || { echo "Missing: $1 — $2"; exit 1; }; }
need python3 "install Python 3.9 or later (Mac: brew install python)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Python 3.9 or later is needed; this is $(python3 --version)"; exit 1; }
[ -n "$LOCAL" ] || need docker "install OrbStack or Docker Desktop. Or without Docker, on a Mac: ./mac.sh"

# A versioned Homebrew MariaDB (mariadb@11.8) is not on the PATH: find its client.
if [ -n "$LOCAL" ] && ! command -v mariadb >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
  for formula in "${MARIADB_FORMULA:-mariadb@11.8}" mariadb@11.8 mariadb; do
    bin="$(brew --prefix "$formula" 2>/dev/null)/bin"
    if [ -x "$bin/mariadb" ]; then PATH="$bin:$PATH"; export PATH; break; fi
  done
fi

echo "1/6  Python packages"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ -z "$LOCAL" ]; then
  echo "2/6  MariaDB in Docker"
  docker compose up -d --wait
else
  echo "2/6  The MariaDB on this machine"
  if [ -n "$TUNE" ]; then
    need brew "--tune is for a Homebrew MariaDB. Elsewhere, put the lines it would write in your own configuration"
    if [ "$(mariadb -N -e 'SELECT @@innodb_buffer_pool_size >= 536870912 AND @@mhnsw_max_cache_size >= 134217728' 2>/dev/null)" = 1 ]; then
      echo "     it already has the memory settings"
    else
      FORMULA="${MARIADB_FORMULA:-$(brew services list 2>/dev/null | awk '$1 ~ /^mariadb/ {print $1; exit}')}"
      ETC="$(brew --prefix)/etc"
      mkdir -p "$ETC/my.cnf.d"
      # loose-: a server that does not know the setting ignores it and still starts
      printf '%s\n' "# Written by Rabbit Hole (setup.sh --tune). To undo: delete this file and restart MariaDB." \
        "[mariadbd]" "bind-address = 127.0.0.1" "innodb_buffer_pool_size = 1G" "loose-mhnsw_max_cache_size = 512M" > "$ETC/my.cnf.d/rabbithole.cnf"
      # Homebrew's my.cnf reads my.cnf.d; if this one does not, add the line that makes it
      if [ ! -f "$ETC/my.cnf" ] || ! grep -q '^!includedir' "$ETC/my.cnf"; then
        printf '\n!includedir %s/my.cnf.d\n' "$ETC" >> "$ETC/my.cnf"
      fi
      echo "     wrote $ETC/my.cnf.d/rabbithole.cnf (1 GB buffer pool, 512 MB vector index cache, local connections only)"
      echo "     restarting MariaDB (${FORMULA:-mariadb})"
      brew services restart "${FORMULA:-mariadb}" >/dev/null
      for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
        mariadb -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1
      done
    fi
  fi
  if [ ! -f .env ]; then
    echo "DB_PORT=3306" > .env
    echo "     wrote .env with DB_PORT=3306, the usual local port (change it there if yours differs)"
  fi
  rc=0; .venv/bin/python pipeline/check_db.py >/dev/null 2>&1 || rc=$?
  if [ "$rc" = 2 ]; then      # cannot connect: the database or the user is probably not there yet
    # Local connections only: the password is no secret, so the account must not be reachable from the network.
    SQL="CREATE DATABASE IF NOT EXISTS rabbithole CHARACTER SET utf8mb4;
         CREATE DATABASE IF NOT EXISTS rabbithole_test CHARACTER SET utf8mb4;"
    for host in localhost 127.0.0.1 ::1; do
      SQL="$SQL
         CREATE USER IF NOT EXISTS 'rabbithole'@'$host' IDENTIFIED BY 'rabbithole';
         GRANT ALL ON rabbithole.* TO 'rabbithole'@'$host'; GRANT ALL ON rabbithole_test.* TO 'rabbithole'@'$host';"
    done
    echo "     making the database and the user 'rabbithole' (as your own MariaDB login)"
    if ! { command -v mariadb >/dev/null && { mariadb -e "$SQL" 2>/dev/null || mariadb -uroot -e "$SQL" 2>/dev/null; }; }; then
      echo "     Could not. Is MariaDB running? Otherwise run this as a MariaDB admin, then ./setup.sh --local again:"
      echo "$SQL"
      exit 1
    fi
  fi
fi
.venv/bin/python pipeline/check_db.py

echo "3/6  The datasets: S (today's text, ready-made vectors) and M (every version since 2003) — 460 MB, resumable"
.venv/bin/python pipeline/download.py --titles

echo "4/6  Read all 862,076 versions: every link, when it appeared, when it went (2 to 4 minutes)"
if [ -f data/graph/.done ]; then echo "     already done"; else .venv/bin/python pipeline/extract.py data/m data/graph; fi

echo "5/6  Load MariaDB: system-versioned tables with Wikipedia's own dates, and the vector index"
.venv/bin/python pipeline/load.py
.venv/bin/python pipeline/analyze.py

echo "6/6  The two models (optional)"
if [ -n "$SKIP_MODELS" ]; then
  echo "     left to mac.sh, which gets them after the app is open"
elif command -v ollama >/dev/null && ollama list >/dev/null 2>&1; then
  echo "     bge-m3 turns a question into a vector (1.2 GB the first time)"
  ollama pull bge-m3 || echo "     could not pull bge-m3: questions will be matched by keywords"
  if ollama list | grep -qi gemma; then echo "     a Gemma model is here: it will write the answers"
  else echo "     no Gemma model yet. For written answers:  ollama pull gemma4   (or point LLM_BASE_URL in .env at any OpenAI-compatible endpoint)"; fi
else
  echo "     Ollama is not running. Everything works without it except two things: questions are matched by"
  echo "     keywords instead of meaning, and answers are not written out. To add them: install Ollama"
  echo "     (https://ollama.com/download), then:  ollama pull bge-m3 && ollama pull gemma4"
fi

if [ -z "$SKIP_MODELS" ]; then
  echo
  echo "Ready. Start it:  .venv/bin/python -m rabbithole   then open http://localhost:8000"
fi
