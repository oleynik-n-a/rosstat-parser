#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
rosstat_python=""
for candidate in python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
        rosstat_python="$candidate"
        break
    fi
done
if [ -z "$rosstat_python" ]; then
    echo 'Нужен Python 3.12 или новее. На macOS с Homebrew: brew install python@3.12'
    exit 2
fi
"$rosstat_python" -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-local.txt
.venv/bin/python local_check.py "$@"
