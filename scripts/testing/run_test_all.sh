#!/usr/bin/env bash
# Run the entire non-REST test suite against BOTH CAD backends and report
# both outcomes (report-both: neither backend aborts the other). Invoked by
# the `test-all` task in the fem env.
#
#   - pythonocc (pyocc): runs here, in the already-active fem env.
#   - ada-cpp:           runs in the tests-adacpp env via its own `test-all`.
#
# Exits non-zero if EITHER backend had failures, but always runs both so you
# get both result sets in one go. comms/rest is out of scope (viewer-api env).
set -u
root="${PIXI_PROJECT_ROOT:-$PWD}"

echo "######## test-all [1/2]: ada-cpp backend (tests-adacpp env) ########"
pixi run -e tests-adacpp test-all
rc_adacpp=$?

echo "######## test-all [2/2]: pythonocc backend (fem env) ########"
PYTHONPATH="$root/src" pytest tests --ignore=tests/comms/rest --durations=0
rc_pyocc=$?

echo
echo "######## test-all summary ########"
printf 'ada-cpp   (tests-adacpp): %s\n' "$([ "$rc_adacpp" -eq 0 ] && echo PASS || echo "FAIL (exit $rc_adacpp)")"
printf 'pythonocc (fem):          %s\n' "$([ "$rc_pyocc" -eq 0 ] && echo PASS || echo "FAIL (exit $rc_pyocc)")"

[ "$rc_adacpp" -eq 0 ] && [ "$rc_pyocc" -eq 0 ]
