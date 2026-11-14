#!/usr/bin/env bash
# Host report for the gate: which lanes can run here, and which are missing a
# toolchain. Prints the same facts setup_host --check uses.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
rc=0
echo "workspace: $root"
for tool in git bazelisk node npm python3; do
  printf '%-10s %s\n' "$tool" "$(command -v "$tool" 2>/dev/null || echo MISSING)"
done
echo "node       $(node --version 2>/dev/null || echo MISSING)"
echo "bazelisk   $(bazelisk --version 2>/dev/null | head -1 || echo MISSING)"
echo "frontend   $([ -d "$root/frontend/node_modules" ] && echo installed || echo 'NOT installed')"
echo "backend    $([ -x "$root/backend/.venv/bin/pytest" ] && echo installed || echo 'NOT installed')"
echo
"$here/setup_host.sh" --check || rc=1
exit "$rc"
