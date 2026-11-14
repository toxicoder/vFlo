#!/usr/bin/env bash
# Idempotent host bootstrap for the host-delegating gate lanes.
#
# `bazelisk run //tools/bazel:setup_host -- --check` prints what is missing and
# installs nothing; without --check it installs. Version floors are pinned here
# and reported by //tools/bazel:doctor.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

MISSING=()
check_cmd() { command -v "$1" >/dev/null 2>&1 || MISSING+=("$1"); }
check_cmd git
check_cmd bazelisk
check_cmd node
check_cmd npm
check_cmd python3

node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local major
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  [ "$major" -ge 20 ]
}
node_ok || MISSING+=("node>=20 (found $(node --version 2>/dev/null || echo none))")

[ -f "$root/frontend/package-lock.json" ] || MISSING+=("frontend/package-lock.json")
[ -d "$root/frontend/node_modules" ] || MISSING+=("frontend/node_modules")
[ -x "$root/backend/.venv/bin/python" ] || MISSING+=("backend/.venv")
[ -x "$root/backend/.venv/bin/pytest" ] || MISSING+=("backend/.venv pytest")

if [ "${#MISSING[@]}" -gt 0 ]; then
  printf 'missing: %s\n' "${MISSING[@]}"
  if [ "$CHECK_ONLY" = "1" ]; then
    echo "host is NOT ready"
    exit 1
  fi
fi

if [ "$CHECK_ONLY" = "1" ]; then
  echo "host is ready"
  exit 0
fi

if [ ! -d "$root/frontend/node_modules" ]; then
  echo "installing frontend dependencies"
  (cd "$root/frontend" && npm ci --no-audit --no-fund)
fi

if [ ! -x "$root/backend/.venv/bin/pytest" ]; then
  echo "creating backend test venv"
  python3 -m venv "$root/backend/.venv"
  "$root/backend/.venv/bin/python" -m pip install --quiet --upgrade pip
  "$root/backend/.venv/bin/python" -m pip install --quiet -r "$root/backend/requirements-test.txt"
fi

echo "host is ready"
