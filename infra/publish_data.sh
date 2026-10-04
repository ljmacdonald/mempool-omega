#!/usr/bin/env bash
# Publish one file to the `data` branch, which the website reads. The branch is always a single commit (replaced
# every time, so no history builds up); files other workflows put there are kept.
# Usage: GITHUB_TOKEN=... infra/publish_data.sh <local file> <path on the data branch>
set -euo pipefail
SRC="$(realpath "$1" 2>/dev/null || true)"; DEST="$2"
[ -n "$SRC" ] && [ -f "$SRC" ] || { echo "nothing to publish"; exit 0; }
URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
WORK="$(mktemp -d)"
for i in 1 2 3 4; do
  rm -rf "$WORK/repo" && mkdir -p "$WORK/repo" && cd "$WORK/repo"
  git init -q -b data
  git config user.name "mempool-omega-bot"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  if git fetch -q --depth 1 "$URL" data 2>/dev/null; then git checkout -q FETCH_HEAD -- . 2>/dev/null || true; fi
  mkdir -p "$(dirname "$DEST")" && cp "$SRC" "$DEST"
  git add -A && git commit -q -m "data: $DEST $(date -u +%Y-%m-%dT%H:%MZ)"
  if git push -q -f "$URL" HEAD:data; then echo "published $DEST"; exit 0; fi
  sleep $((2 ** i))
done
echo "could not publish $DEST" >&2
exit 1
