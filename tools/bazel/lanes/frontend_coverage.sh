#!/usr/bin/env bash
# Frontend coverage floor, driven through the gate. The floor itself is owned by
# frontend/scripts/verify-coverage.mjs; this lane only runs it and reports.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
gate_require_or_skip "frontend node_modules" "[ -d '$root/frontend/node_modules' ]"
gate_require_or_skip "vitest" "[ -x '$root/frontend/node_modules/.bin/vitest' ]"
cd "$root/frontend"
workers="$(gate_worker_cap 3)"
COVERAGE_STAGE=wave3 ./node_modules/.bin/vitest run --coverage --maxWorkers "$workers"
exec node scripts/verify-coverage.mjs
