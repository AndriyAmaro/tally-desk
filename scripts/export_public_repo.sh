#!/usr/bin/env bash
# Builds a clean, local-only git repository from this directory, ready to push.
# It never touches the network. Usage: scripts/export_public_repo.sh /path/to/new/empty/dir
set -euo pipefail

dest="${1:?usage: export_public_repo.sh DEST_DIR}"
src="$(cd "$(dirname "$0")/.." && pwd)"

if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  echo "refusing to export into a non-empty directory: $dest" >&2
  exit 1
fi
mkdir -p "$dest"

tar -C "$src" \
  --exclude='node_modules' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='./data' --exclude='dist' --exclude='build' --exclude='.venv' \
  -cf - . | tar -C "$dest" -xf -

mkdir -p "$dest/ledger"
touch "$dest/ledger/.gitkeep"

if grep -rIlE --exclude=export_public_repo.sh 'nvapi-|ghp_|github_pat_|BEGIN (RSA|EC|OPENSSH) PRIVATE KEY' "$dest" >/dev/null 2>&1; then
  echo "secret-looking text found, aborting:" >&2
  grep -rIlE --exclude=export_public_repo.sh 'nvapi-|ghp_|github_pat_|BEGIN (RSA|EC|OPENSSH) PRIVATE KEY' "$dest" >&2
  exit 1
fi

cd "$dest"
git init -q -b main
git add -A
name="${PUBLIC_AUTHOR_NAME:-Andri Amaro}"
email="${PUBLIC_AUTHOR_EMAIL:-177476258+AndriyAmaro@users.noreply.github.com}"
GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
  git commit -q -m "Verdict Desk: audit-grade, replayable market decisions"
echo "Exported to $dest ($(git rev-list --count HEAD) commit). Nothing was pushed."
