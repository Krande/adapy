#!/usr/bin/env bash
# The default suite, in both halves, reporting both. Invoked by the `test` task.
#
#   1. the MAIN suite, here in the already-active env. `tests/compat` is excluded: those modules
#      import pythonocc at module scope, which skips at collection before a marker can speak.
#   2. the COMPAT leg, in `tests-xkernel` -- the env that carries BOTH kernels. It runs every
#      `pyocc`-marked test wherever it lives, with ADAPY_REQUIRE_BOTH_KERNELS set so a kernel that
#      fails to import FAILS the leg instead of silently shrinking it to nothing.
#
# Report-both, like `run_test_all.sh`: neither half aborts the other, and the exit code is
# non-zero if either had failures. Running only half of this used to be invisible -- the
# pythonocc-dependent tests reported as ~117 skips in a green run, which is a number nobody reads
# and coverage nobody gets.
set -u
root="${PIXI_PROJECT_ROOT:-$PWD}"

echo "######## test [1/2]: main suite (this env) ########"
PYTHONPATH="$root/src" pytest tests \
    --ignore=tests/profiling \
    --ignore=tests/comms/rest \
    --ignore=tests/compat \
    --durations=0
rc_main=$?

echo "######## test [2/2]: compat leg — both kernels (tests-xkernel env) ########"
pixi run -e tests-xkernel test-compat
rc_compat=$?

echo
echo "######## test summary ########"
printf 'main suite  (this env):      %s\n' "$([ "$rc_main" -eq 0 ] && echo PASS || echo "FAIL (exit $rc_main)")"
printf 'compat leg  (tests-xkernel): %s\n' "$([ "$rc_compat" -eq 0 ] && echo PASS || echo "FAIL (exit $rc_compat)")"

[ "$rc_main" -eq 0 ] && [ "$rc_compat" -eq 0 ]
