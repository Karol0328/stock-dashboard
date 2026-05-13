#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create and activate venv if not present
if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "→ Installing dependencies..."
pip install -q -r requirements.txt

echo "→ Starting stock dashboard on http://localhost:5001"
python3 app.py
