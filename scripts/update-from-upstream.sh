#!/usr/bin/env bash
# Merge upstream/main into this fork, then run the suite.
#
# AppliedIn here tracks https://github.com/sayantan94/AppliedIn.git and takes
# its main regularly. A conflict is aborted rather than resolved: the rules
# that keep a real person's application from going out wrong live in this
# code, and an automatic merge is how one of them changes without anyone
# looking. This script never pushes.
set -euo pipefail
cd "$(dirname "$0")/.."

upstream_url="https://github.com/sayantan94/AppliedIn.git"

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream "$upstream_url"
fi

git fetch upstream

# A merge on top of local edits cannot be unwound cleanly. abort puts the
# index back, but by then the local work and the upstream changes have already
# been mixed in the working tree, and there is no second chance to separate them.
if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing to merge upstream/main: the working tree has uncommitted changes." >&2
  echo "Commit or stash them, then run ./appliedin update again." >&2
  exit 1
fi

if ! git merge --no-edit upstream/main; then
  # The names are only listed while the merge is still in progress. abort
  # clears the unmerged index, which is what we want left behind — after the
  # list has been captured.
  conflicts="$(git diff --name-only --diff-filter=U || true)"
  if git merge --abort; then
    echo "Merge aborted. upstream/main conflicts with this branch and was not applied." >&2
  else
    echo "git merge upstream/main failed, and there was no merge to abort." >&2
  fi
  if [ -n "$conflicts" ]; then
    echo "Conflicting files:" >&2
    printf '%s\n' "$conflicts" >&2
  fi
  exit 1
fi

echo "Merged upstream/main. Running tests…"
.venv/bin/python -m pytest -q
