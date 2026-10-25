#!/usr/bin/env bash
# One live cycle: inputs (daytime) -> grade finals -> shot quality -> model -> validate -> publish.
# Any step may fail without taking the others down; validation failure restores the last good data/ and records it.
set -u
cd "$(dirname "$0")/.."
step() { local name=$1; shift; if "$@"; then echo "::notice::$name ok"; else echo "::warning::$name failed"; FAILED="$FAILED $name"; fi; }
FAILED=""
# pregame inputs around the clock, at most once every ~9 minutes (polite to the sources); FORCE_INPUTS=1 skips the wait
step inputs python3 tools/fetch_inputs.py scheduled
step settle python3 tools/settle_live.py
step shotquality python3 tools/xg_nightly.py
step engine python3 tools/live_engine.py
if ! python3 tools/validate_data.py; then
  echo "::error::validation failed; restoring last good data"
  git checkout -- data/
  python3 tools/mark_run.py "validation failed:$FAILED"
  FAILED="$FAILED validate"
fi
if [ -n "$(git status --porcelain data/)" ]; then
  git config user.name lockski; git config user.email lockski@users.noreply.github.com
  git add data/ && git commit -q -m "live $(date -u +%FT%TZ)${FAILED:+ (failed:$FAILED)} [skip netlify]"
  for i in 1 2 3; do git pull -q --rebase origin main && git push -q origin main && break; sleep 5; done
else echo "no change"; fi
[ -z "$FAILED" ]
