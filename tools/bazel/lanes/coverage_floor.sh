#!/usr/bin/env bash
# The gate's own Python must be covered to 100%: these four scripts decide
# whether the repo is Bazel-first, so a branch of them nobody tests is a hole in
# the definition of done, not in an application feature.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
# pytest and pytest-cov live in the backend venv, which is the same interpreter
# the backend suite uses; fall back to whatever python3 has them.
py="$root/backend/.venv/bin/python"
if [ ! -x "$py" ]; then
  py="$(command -v python3)"
fi
gate_require_or_skip "pytest and pytest-cov" "'$py' -c 'import pytest, pytest_cov'"
cd "$root/tools/bazel"
exec "$py" -m pytest tests --cov=build_files --cov=docs_gate \
  --cov=glob_budget --cov=owned_files_check --cov-report=term-missing \
  --cov-fail-under=100 -q
