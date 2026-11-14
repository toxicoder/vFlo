#!/usr/bin/env bash
# The gate's own Python must be covered to 100%: these three scripts decide
# whether the repo is Bazel-first, so a branch of them nobody tests is a hole in
# the definition of done, not in an application feature.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
gate_require_or_skip "pytest" "command -v python3 && python3 -c 'import pytest, pytest_cov'"
cd "$root/tools/bazel"
exec python3 -m pytest tests --cov=build_files --cov=glob_budget \
  --cov=docs_gate --cov=owned_files_check --cov-report=term-missing --cov-fail-under=100 -q
