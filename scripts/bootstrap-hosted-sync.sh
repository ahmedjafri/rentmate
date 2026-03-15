#!/usr/bin/env bash
# One-time script to bootstrap sync when core and hosted have unrelated histories.
# Usage: HOSTED_REPO=owner/repo ./scripts/bootstrap-hosted-sync.sh

set -euo pipefail

HOSTED_REPO="${HOSTED_REPO:?Set HOSTED_REPO=owner/repo}"
CORE_REPO_URL=$(git remote get-url origin)

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "Cloning hosted repo..."
git clone "git@github.com:${HOSTED_REPO}.git" "$WORKDIR/hosted"
cd "$WORKDIR/hosted"

echo "Fetching core..."
git remote add core "$CORE_REPO_URL"
git fetch core main

echo "Merging with --allow-unrelated-histories..."
if git merge core/main --no-edit --allow-unrelated-histories; then
  echo "Merge clean. Pushing to hosted main..."
  git push origin main
else
  echo "Conflicts detected. Resolve them in: $WORKDIR/hosted"
  echo "Then run: git push origin main"
  trap - EXIT  # keep the workdir so you can resolve
  exit 1
fi

echo "Done."
