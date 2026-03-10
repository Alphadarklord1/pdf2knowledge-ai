#!/bin/zsh
set -euo pipefail
REPO_NAME="${1:-pdf2knowledge-ai}"
if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required."
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub auth is not ready. Run: gh auth login -h github.com"
  exit 1
fi
if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
else
  gh repo create "$REPO_NAME" --public --source=. --remote=origin --push
fi
