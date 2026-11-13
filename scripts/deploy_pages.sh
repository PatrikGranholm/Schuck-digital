#!/usr/bin/env bash
# Build the reader and publish it to the gh-pages branch of this repository's `origin`.
#
#   scripts/deploy_pages.sh            build + commit + push
#   scripts/deploy_pages.sh --dry-run  build + commit locally, no push
#
# site/ (gitignored on main) is its own git repository on branch gh-pages, so the page
# images never enter main. History is kept, so unchanged images are not uploaded again.
# One-time GitHub setup after the first push:
#   gh api -X POST repos/{owner}/{repo}/pages -f 'source[branch]=gh-pages' -f 'source[path]=/'
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-../schlyter-digital/.venv/bin/python}
REMOTE=$(git remote get-url origin 2>/dev/null) || { echo "no 'origin' remote on main; add one first" >&2; exit 1; }
MAIN=$(git rev-parse --short HEAD 2>/dev/null || echo "uncommitted")

"$PY" scripts/build_site.py

cd site
if [ ! -d .git ]; then
  git init -q -b gh-pages
  git remote add origin "$REMOTE"
  git fetch -q origin gh-pages 2>/dev/null && git reset -q --soft origin/gh-pages || true
fi
git add -A
if git diff --cached --quiet; then
  echo "site unchanged; nothing to publish"
else
  git commit -q -m "Site build from main ${MAIN} ($(date -I))"
  echo "committed: $(git log --oneline -1)"
fi
size=$(du -sh . --exclude=.git | cut -f1)
echo "site size: ${size} (GitHub Pages limit: 1 GB)"
if [ "${1:-}" != "--dry-run" ]; then
  git push -u origin gh-pages
fi
