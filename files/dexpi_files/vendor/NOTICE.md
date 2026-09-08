# Vendored DEXPI test cases

The two files in this directory are official DEXPI example P&IDs, vendored unmodified so the DEXPI
tests in `tests/core/cadit/dexpi/` have real, offline, third-party corpus files to run against —
not just documents adapy generated itself.

| File | Test case | Notes |
|---|---|---|
| `P01V01-VER.EX01.xml` | P01 "Pipe FromTo Nozzles" | Two equipment, one nozzle-to-nozzle piping connection. |
| `E01V02-VER.EX01.xml` | E01 "Tank" | A tank with two nested chambers. |

## Source

- Repository: <https://gitlab.com/dexpi/TrainingTestCases>
- Commit: `a23d61e2e089eb2ca464cd552f9ae580a2785963`
- Original paths (DEXPI 1.3 example set):
  - `dexpi 1.3/example pids/P01 Pipe FromTo Nozzles/P01V01-VER.EX01.xml`
  - `dexpi 1.3/example pids/E01 Tank/E01V02-VER.EX01.xml`

## Licence

Creative Commons Attribution 4.0 International (CC BY 4.0) —
<https://creativecommons.org/licenses/by/4.0/>

Copyright DEXPI e.V. (<https://dexpi.org/>). No changes have been made to either file beyond
copying it out of the source repository.
