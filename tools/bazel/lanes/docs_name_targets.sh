#!/usr/bin/env bash
# Gate: docs and CLI text name //targets, never a script path.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
gate_require_or_skip "bazelisk" "command -v bazelisk"
exec python3 "$root/tools/bazel/docs_gate.py" --root "$root"
