#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
gate_require_or_skip "frontend node_modules" "[ -d '$root/frontend/node_modules' ]"
gate_require_or_skip "vitest" "[ -x '$root/frontend/node_modules/.bin/vitest' ]"
workers="$(gate_worker_cap 3)"
cd "$root/frontend" && exec ./node_modules/.bin/vitest run --maxWorkers "$workers"
