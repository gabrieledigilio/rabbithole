#!/bin/bash
# Rabbit Hole on a Mac, in one command:   ./mac.sh
#
#   1. MariaDB: Docker if it is running here; otherwise Homebrew's mariadb@11.8 (the series this
#      was built and measured on), installed if needed and started as a Homebrew service.
#   2. Ollama, if it is installed: started if it is not answering.
#   3. ./setup.sh: Python packages, the data, the load. First time only.
#   4. Starts the app and opens it in the browser.
#   5. The models, last on purpose: the app is already open, and Ctrl-C skips them.
#      bge-m3 (1.2 GB) turns a question into a vector; if Ollama has no chat model yet,
#      Gemma 4 (several GB) to write the answers.
#
#   ./mac.sh            the first time: all of the above. Afterwards: starts the app, in seconds.
#   ./mac.sh --setup    run the setup step again (after new data or new code)
#   ./mac.sh --stop     stop the app
#
# Everything it prints also goes to setup.log, next to this file. It changes these things outside
# this folder: Homebrew packages (mariadb@11.8 and what it needs), two Homebrew services that also
# start at login (undo: brew services stop mariadb@11.8 ollama), one file in Homebrew's my.cnf.d,
# and the models in Ollama's own folder.
set -o pipefail
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
LOG="$PWD/setup.log"

say()  { printf '\n== %s\n' "$*"; }
fail() {
  printf '\n!! STOPPED: %s\n' "$*"
  printf '!! Everything printed so far is in %s\n' "$LOG"
  printf '!! Send Claude the lines from the last "==" down to here.\n'
  exit 1
}
alive() { [ -f .app.pid ] && kill -0 "$(cat .app.pid)" 2>/dev/null; }

stop_app() {
  if alive; then kill "$(cat .app.pid)" 2>/dev/null; sleep 1; echo "stopped the app"; fi
  rm -f .app.pid
}

main() {
  echo "Rabbit Hole on $(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null), $(uname -m), $(date '+%Y-%m-%d %H:%M')"
  # A terminal that never loaded Homebrew's settings does not have it on the PATH
  if ! command -v brew >/dev/null 2>&1; then
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
    done
  fi
  command -v python3 >/dev/null || fail "python3 is not installed. With Homebrew: brew install python"
  echo "python3 is $(python3 --version 2>&1 | cut -d' ' -f2) at $(command -v python3)"

  # ---------------------------------------------------------------- 1. MariaDB
  if docker info >/dev/null 2>&1; then
    say "MariaDB: Docker is running here, so it goes in a container"
    docker compose up -d --wait || fail "docker compose up"
    SETUP_ARGS=""
  elif command -v brew >/dev/null 2>&1; then
    # a Homebrew MariaDB that is already a service, whatever its version; otherwise 11.8
    FORMULA=$(brew services list 2>/dev/null | awk '$1 ~ /^mariadb/ {print $1; exit}')
    FORMULA=${FORMULA:-mariadb@11.8}
    if brew list --versions "$FORMULA" >/dev/null 2>&1; then
      say "MariaDB: Homebrew's $FORMULA is installed"
    else
      say "MariaDB: installing Homebrew's $FORMULA (a few minutes)"
      brew install "$FORMULA" || fail "brew install $FORMULA did not finish"
    fi
    export MARIADB_FORMULA="$FORMULA"
    PATH="$(brew --prefix "$FORMULA")/bin:$PATH"; export PATH      # a versioned formula is not on the PATH
    if ! mariadb-admin ping --silent >/dev/null 2>&1; then
      echo "starting it as a Homebrew service"
      brew services start "$FORMULA" || fail "brew services start $FORMULA"
      for i in $(seq 1 40); do mariadb-admin ping --silent >/dev/null 2>&1 && break; sleep 1; done
      mariadb-admin ping --silent >/dev/null 2>&1 ||
        fail "MariaDB was started but does not answer. Its own log is the newest .err file in $(brew --prefix)/var/mysql"
    fi
    echo "MariaDB is up: $(mariadb -N -e 'SELECT VERSION()' 2>/dev/null || mariadb -uroot -N -e 'SELECT VERSION()' 2>/dev/null)"
    SETUP_ARGS="--local --tune"
  else
    fail "neither Docker nor Homebrew is here. Install Homebrew (https://brew.sh) or OrbStack (https://orbstack.dev), then run ./mac.sh again"
  fi

  # ---------------------------------------------------------------- 2. Ollama
  if command -v ollama >/dev/null 2>&1; then
    if ! ollama list >/dev/null 2>&1; then
      say "Ollama: installed but not running, starting it"
      STARTED=""
      if command -v brew >/dev/null 2>&1 && brew list --versions ollama >/dev/null 2>&1; then
        brew services start ollama >/dev/null 2>&1 && STARTED=1
      elif [ -d /Applications/Ollama.app ]; then
        open -ga Ollama && STARTED=1
      fi
      if [ -n "$STARTED" ]; then
        for i in $(seq 1 25); do ollama list >/dev/null 2>&1 && break; sleep 1; done
      fi
    fi
    if ollama list >/dev/null 2>&1; then echo "Ollama is up"
    else echo "Ollama did not start. Not fatal: questions are matched by keywords and no answers are written."; fi
  else
    echo "Ollama is not installed. Not fatal: questions are matched by keywords and no answers are written. (brew install ollama)"
  fi

  # ---------------------------------------------------------------- 3. Setup
  if [ -f .setup-done ] && [ -z "$AGAIN" ]; then
    say "Setup was done before (./mac.sh --setup runs it again)"
  else
    say "Setup: ./setup.sh $SETUP_ARGS"
    # shellcheck disable=SC2086
    SKIP_MODELS=1 ./setup.sh $SETUP_ARGS || fail "setup.sh stopped; its last lines are just above"
    touch .setup-done
  fi

  # ---------------------------------------------------------------- 4. The app
  say "Starting the app"
  stop_app
  PORT=$(.venv/bin/python -c '
import socket
for port in (8000, 8010, 8020, 8030):
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            print(port)
            break')
  [ -n "$PORT" ] || fail "ports 8000, 8010, 8020 and 8030 are all in use"
  # In a session of its own, so closing this window or pressing Ctrl-C here does not stop it.
  PORT=$PORT .venv/bin/python -c '
import os, sys
os.setsid()
os.execv(sys.executable, [sys.executable, "-m", "rabbithole"])' > app.log 2>&1 < /dev/null &
  echo $! > .app.pid
  for i in $(seq 1 40); do curl -s -m 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break; sleep 1; done
  STATUS=$(curl -s -m 10 "http://localhost:$PORT/api/status") || fail "the app did not start. The end of app.log: $(tail -n 5 app.log)"
  open "http://localhost:$PORT"

  say "Ready: http://localhost:$PORT"
  echo "$STATUS" | .venv/bin/python -c '
import json, sys
s = json.load(sys.stdin)
print("  MariaDB", s["mariadb"].split("-")[0])
print("  questions become vectors:", "yes, with " + s["embed_model"] if s["embed"] else "no (Ollama with bge-m3 is not answering): keyword search")
print("  answers are written by:", s["llm"] or "nothing yet")'
  echo "  stop it:         $PWD/mac.sh --stop"
  echo "  start it again:  $PWD/mac.sh"
  echo "  logs:            $PWD/setup.log and app.log"

  # ---------------------------------------------------------------- 5. The models, last
  if ollama list >/dev/null 2>&1; then
    if ! ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -q '^bge-m3'; then
      say "The app is open. Now downloading bge-m3, which turns a question into a vector (1.2 GB)"
      ollama pull bge-m3 && echo "Done. Search by meaning switches itself on within a minute." ||
        echo "Not downloaded: questions stay matched by keywords. Later: ollama pull bge-m3"
    fi
    CHAT=$(ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -viE 'embed|bge|minilm|rerank' | head -n 1)
    if [ -z "$CHAT" ]; then
      GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
      FREE=$(df -g "$HOME" 2>/dev/null | awk 'NR == 2 {print $4}')
      TAG=gemma4; [ "$GB" -lt 16 ] && TAG=gemma4:e2b
      if [ "${FREE:-0}" -lt 25 ]; then
        echo; echo "Only ${FREE:-?} GB free on this disk, so no answer model was downloaded. When there is room: ollama pull $TAG"
      else
        say "Last step: downloading $TAG to write the answers (several GB). The app is already open; Ctrl-C skips this."
        ollama pull "$TAG" && echo "Done. The app picks the model up by itself within a minute." || echo "Not downloaded. Later: ollama pull $TAG"
      fi
    fi
  fi
}

case "${1:-}" in
  --stop)  stop_app; exit 0 ;;
  --setup) AGAIN=1 ;;
  "")      AGAIN="" ;;
  *)       echo "Unknown option: $1   (see the top of mac.sh)"; exit 1 ;;
esac
main 2>&1 | tee -a "$LOG"
exit "${PIPESTATUS[0]}"
