#!/usr/bin/env bash
# Commit state/ (ledger, models, reports, logs) back to the current branch. Safe to run when nothing changed.
set -euo pipefail
MSG="${1:-state update}"
BRANCH="${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
git config user.name  "mempool-omega-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
# keep the JSON log from growing without bound (last 5000 lines)
if [ -f state/logs/omega.log ]; then tail -n 5000 state/logs/omega.log > state/logs/omega.log.tmp && mv state/logs/omega.log.tmp state/logs/omega.log; fi
if [ -f state/logs/alerts.log ]; then tail -n 2000 state/logs/alerts.log > state/logs/alerts.log.tmp && mv state/logs/alerts.log.tmp state/logs/alerts.log; fi
git add state/
if git diff --cached --quiet; then
  echo "no state changes"; exit 0
fi
git commit -q -m "$MSG [skip ci]"
for i in 1 2 3 4; do
  if git push origin "HEAD:${BRANCH}"; then exit 0; fi
  echo "push failed (attempt $i); rebasing on remote and retrying"
  sleep $((2 ** i))
  git pull --rebase -X theirs origin "${BRANCH}" || true
done
echo "could not push state after retries" >&2
exit 1
