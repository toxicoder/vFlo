#!/usr/bin/env bash
# Gate: no unannotated glob(), and never more glob() than the recorded budget.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
budget_file="$root/tools/bazel/glob-budget.txt"
[ -f "$budget_file" ] || { echo "error: missing $budget_file" >&2; exit 2; }
max="$(tr -dc '0-9' < "$budget_file")"
[ -n "$max" ] || { echo "error: $budget_file holds no number" >&2; exit 2; }
exec python3 "$root/tools/bazel/glob_budget.py" --root "$root" --max "$max"
