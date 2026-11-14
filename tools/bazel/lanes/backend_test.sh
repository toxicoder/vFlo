#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
py="$(gate_backend_python "$root")"
gate_require_or_skip "backend/.venv" "[ -x '$py' ]"
gate_require_or_skip "pytest" "[ -x '$root/backend/.venv/bin/pytest' ]"
cd "$root/backend" && exec "$root/backend/.venv/bin/pytest"
