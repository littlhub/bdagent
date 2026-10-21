#!/usr/bin/env bash
# ===================================================================
#  DBAgent launcher (Linux / 容器)
#  location: dbagent/run.sh      entry: dbagent/package/main.py
#
#  usage:
#    ./run.sh                  -> package/main.py  (menu / chat)
#    ./run.sh --setup          -> configure API Key
#    ./run.sh plan  "req"      -> plan only, no code change
#    ./run.sh apply "req"      -> plan + generate edits, apply after confirm
#    ./run.sh chat             -> AI chat dev mode
#    ./run.sh <project-path>   -> analyze structure (report + mindmap)
#         extras: --no-mind  --no-open  --out <dir>
#
#  API Key: env var AGENT_API_KEY, or run ./run.sh --setup first
# ===================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

ENTRY="package/main.py"

# ---- locate python: prefer python3, then python ----
PYCMD=""
for C in python3 python; do
    if command -v "$C" >/dev/null 2>&1; then
        PYCMD="$C"
        break
    fi
done

if [ -z "$PYCMD" ]; then
    echo
    echo "[ERROR] Python not found."
    echo "        Install Python 3.10+ or set PYCMD in this script."
    echo
    exit 9009
fi

# ---- force UTF-8 so Chinese output is not garbled ----
export PYTHONUTF8=1
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

echo
echo "============================================================"
echo "  DBAgent"
echo "  entry : $ENTRY"
echo "  python: $PYCMD ($($PYCMD -V 2>&1))"
echo "  cwd   : $PWD"
echo "============================================================"
echo

"$PYCMD" "$ENTRY" "$@"
CODE=$?

if [ "$CODE" -ne 0 ]; then
    echo
    echo "[exit code $CODE]"
fi

# keep window open on double-click (no args)
if [ "$#" -eq 0 ]; then
    echo
    read -r -p "按回车退出..." _
fi
exit "$CODE"
