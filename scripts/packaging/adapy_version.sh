#!/usr/bin/env sh
# Print adapy's version from pyproject.toml ([project].version, the single source of truth the
# release tooling bumps). CI exports it as ADAPY_VERSION so builds that run adapy off PYTHONPATH
# (the docs/report, the images) stamp it explicitly instead of resolving it at runtime (#464).
# Plain sed so it runs on any runner or image without a Python of its own.
set -eu
root="$(cd "$(dirname "$0")/../.." && pwd)"
v="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*["'\'']([^"'\'']+)["'\''].*/\1/p' "$root/pyproject.toml" | head -n 1)"
if [ -z "$v" ]; then
    echo "adapy_version.sh: no version in $root/pyproject.toml" >&2
    exit 1
fi
printf '%s\n' "$v"
