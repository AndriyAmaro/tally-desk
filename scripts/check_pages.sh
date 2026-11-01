#!/usr/bin/env bash
# Smoke-check the published pages by rendering them in headless Chromium.
#
#   bash scripts/check_pages.sh [SITE_DIR] [PORT]
#
# Why this exists: the dashboard, the lab and the verify page are JavaScript that no Python test
# can execute. A page that renders nothing still returns HTTP 200, so a broken page ships silently.
# This serves the site the way GitHub Pages does (web/ plus ledger/ side by side), renders each
# page with real headless Chromium, and asserts on what actually arrived in the DOM.
#
# Requires a Chromium binary. Set CHROME=/path/to/chrome if it is not in a Playwright cache.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${1:-$(mktemp -d)}"
PORT="${2:-$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1]);s.close()')}"
CHROME="${CHROME:-$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | tail -1)}"
[ -n "$CHROME" ] || { echo "no Chromium found; set CHROME=/path/to/chrome" >&2; exit 2; }
SITE="$(cd "$SITE" 2>/dev/null && pwd || echo "$SITE")"
cd "$ROOT"   # so `python3 -m verdict_desk` resolves; the generators run from the repo root
echo "site: $SITE (port $PORT)"

# Mirror the Pages layout: web/ contents at the root, ledger/ beside them.
mkdir -p "$SITE"
cp -r "$ROOT"/web/. "$SITE"/
rm -rf "$SITE/ledger"
cp -r "$ROOT"/ledger "$SITE/ledger"
python3 -m verdict_desk --ledger "$ROOT/ledger/ledger.jsonl" scorecard --out "$SITE/scorecard.html" >/dev/null
python3 -m verdict_desk --ledger "$ROOT/ledger/ledger.jsonl" exhibit --recent 3 --outdir "$SITE/exhibit" >/dev/null

# A stale server on a fixed port serves a different directory and every check lies. Bind a free
# port, and prove the server we reach is ours by fetching a marker only our SITE contains.
python3 -m http.server "$PORT" --directory "$SITE" >/dev/null 2>&1 &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null' EXIT
MARK="probe-$$-$(date +%s)"
printf '%s\n' "$MARK" > "$SITE/_probe"
for _ in $(seq 1 20); do
  sleep 0.3
  if [ "$(curl -s "http://localhost:$PORT/_probe" 2>/dev/null)" = "$MARK" ]; then break; fi
done
if [ "$(curl -s "http://localhost:$PORT/_probe" 2>/dev/null)" != "$MARK" ]; then
  echo "the server on port $PORT is not serving $SITE; aborting rather than reporting lies" >&2
  exit 2
fi

fail=0
render() { timeout 90 "$CHROME" --headless --disable-gpu --no-sandbox --virtual-time-budget=8000 \
  --dump-dom "http://localhost:$PORT/$1" 2>/dev/null; }
# Render once into a file, then match. Piping straight into `grep -q` makes grep exit on the
# first hit, Chromium takes SIGPIPE, and `set -o pipefail` reports the whole pipeline as failed
# even though the needle was found.
dom() { local page="$1"; local out; out=$(mktemp); render "$page" > "$out"; echo "$out"; }

check() { # check <page> <needle> <label>
  local page="$1" needle="$2" label="$3"
  local file; file=$(dom "$page")
  if grep -q -- "$needle" "$file"; then
    rm -f "$file"
    echo "ok   $label"
  else
    echo "FAIL $label ($page did not contain: $needle)"
    cp "$file" "/tmp/page-fail-$page"
    rm -f "$file"
    fail=1
  fi
}

check index.html "recomputed in this browser" "dashboard recomputes hashes"
check index.html "printable exhibit" "dashboard links each decision to its exhibit"
check index.html "sized LONG or SHORT" "dashboard shows how many dossiers commit"
check lab.html "policy.js" "lab loads the shared policy"
check lab.html "printable exhibit" "lab links the selected decision to its exhibit"
check verify.html "Verify a record you were handed" "verify page renders"
check verify.html "sample=1" "verify page can prefill from the ledger"
check scorecard.html "What this table does not tell you" "scorecard prints its caveats"
check exhibit/index.html "Decision exhibits" "exhibit index renders"
check narrative.html "the multi-KOL narrative agent" "market voices page renders"
check narrative.html "Convergences, in order" "market voices lists convergences"

# The verify page must actually verify when driven: ?sample=1 loads two real records.
file=$(dom "verify.html?sample=1")
if grep -q "VERIFIED" "$file"; then
  rm -f "$file"
  echo "ok   verify page returns VERIFIED for real records"
else
  echo "FAIL verify page did not return VERIFIED for real records"
  cp "$file" /tmp/page-fail-verify-sample
  rm -f "$file"
  fail=1
fi

[ "$fail" -eq 0 ] && echo "all pages ok" || echo "some pages failed"
exit "$fail"
