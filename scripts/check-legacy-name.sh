#!/usr/bin/env bash
# Fail if a legacy project name (docs/adr/0004-rename-to-iceblink.md) appears outside the
# allow-list: rename ADRs, CHANGELOG.md, results/, docs/troubleshooting.md and this script.
set -euo pipefail

pattern='floe|rocketlake'
allow=(
  ':(exclude,glob)docs/adr/*-rename-to-*.md'
  ':(exclude)CHANGELOG.md'
  ':(exclude)results/'
  ':(exclude)docs/troubleshooting.md'
  ':(exclude)scripts/check-legacy-name.sh'
)

status=0
hits="$(git grep -n -I -i -w -E --untracked "$pattern" -- . "${allow[@]}")" || status=$?
names="$(git ls-files --cached --others --exclude-standard -- . "${allow[@]}" \
  | grep -i -w -E "$pattern" || true)"

if [ "$status" -gt 1 ]; then
  echo "check-legacy-name: git grep failed with exit $status" >&2
  exit "$status"
fi
if [ "$status" -eq 0 ] || [ -n "$names" ]; then
  echo "check-legacy-name: legacy names found outside the allow-list:" >&2
  [ -n "$hits" ] && echo "$hits" >&2
  [ -n "$names" ] && printf '%s\n' "$names" | sed 's/^/file name: /' >&2
  echo "Rename them as docs/adr/0004-rename-to-iceblink.md describes." >&2
  exit 1
fi
echo "check-legacy-name: no legacy names outside the allow-list"
