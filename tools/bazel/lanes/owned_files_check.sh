#!/usr/bin/env bash
# Gate: every file `git ls-files` reports is owned by a target or ledger-excluded.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
gate_require_or_skip "bazelisk" "command -v bazelisk"
exec python3 "$root/tools/bazel/owned_files_check.py" --root "$root" --strict-ledger
