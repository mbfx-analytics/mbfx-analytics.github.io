#!/usr/bin/env bash
# Daily refresh: clean + reconcile the newest data, build, run every check, export,
# and (optionally) publish to a GitHub Pages repo. Stops at the first failure, so a
# bad data day never replaces the published dashboard.
#
#   scripts/daily_refresh.sh                      # refresh + export only
#   PAGES_DIR=~/code/mbfx-dashboard scripts/daily_refresh.sh   # …and publish
#
# PAGES_DIR must be a local clone of the GitHub Pages repository (index.html at its root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
LOG="logs/refresh-$(date +%Y-%m-%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "== MBFX dashboard refresh $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "== 1/3 Prepare data (validate, remove Demo Competition, reconcile)"
npm run --silent data

echo "== 2/3 Build, verify (headless checks) and export"
npm run --silent export

AS_OF="$(node -e 'const d=require("./src/data/report.json");console.log(d.trend.dates[d.days-1])')"

if [[ -n "${PAGES_DIR:-}" ]]; then
  echo "== 3/3 Publish to $PAGES_DIR"
  cp exports/github-pages/index.html exports/github-pages/.nojekyll "$PAGES_DIR"/
  cd "$PAGES_DIR"
  git add index.html .nojekyll
  if git diff --cached --quiet; then
    echo "No changes to publish."
  else
    git commit -m "Dashboard data through $AS_OF"
    git push
    echo "Published: data through $AS_OF"
  fi
else
  echo "== 3/3 Skipped publish (set PAGES_DIR to publish)"
fi

echo "== Done. Data through $AS_OF · log: $LOG"
