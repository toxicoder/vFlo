#!/usr/bin/env bash
# Backend coverage, driven through the gate; the report line is what a reviewer
# reads, so print it rather than only the pass/fail.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
py="$(gate_backend_python "$root")"
gate_require_or_skip "backend/.venv" "[ -x '$py' ]"
gate_require_or_skip "pytest-cov" "[ -x '$root/backend/.venv/bin/py.test' ] || '$py' -c 'import pytest_cov'"
cd "$root/backend"
# Deselected for the same reason CI deselects it: the torch stack is out of
# requirements-test.txt, so coverage measures the lane CI actually runs.
exec "$root/backend/.venv/bin/pytest" -m "not requires_torch" --cov --cov-report=term-missing
