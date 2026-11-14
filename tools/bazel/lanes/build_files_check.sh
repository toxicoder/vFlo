#!/usr/bin/env bash
# Gate: the checked-in BUILD.bazel files match a fresh render of the manifest.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gate_support.sh
source "$here/gate_support.sh"
root="$(gate_root)"
exec python3 "$root/tools/bazel/build_files.py" --root "$root" --check
