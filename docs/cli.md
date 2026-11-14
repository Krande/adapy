# Command line interface

The distribution is named `ada-py`, but the console script it installs is
`ada` — there is no `ada-py` command. Install the package with [pixi](https://pixi.sh),
then call `ada`:

```bash
pixi global install ada-py
ada --help
```

In a pixi project (`pixi add ada-py`), run it through the project environment instead:
`pixi run ada --help`.

In a checkout of the adapy repository, `pixi run ada ...` runs the CLI from the
checked-out source (`src/` on `PYTHONPATH`) rather than an installed release. It
defaults to the `fem` environment, the one `pixi run test` uses; pass `-e`
to run it in another environment that carries the `prod` feature, e.g.
`pixi run -e tests ada view model.ifc`.

The entry point lives in its own top-level package (`ada_cli`) so that
`ada --help` does not import the full CAD/FEM surface. Every subcommand
imports its implementation lazily, so an invocation only pays for what it uses.

One global option applies to all of them:

`--log-level`
:   Logging level for the commands that initialise the `ada` package
    (`convert`, `view`, `results`, `audit repro`, `audit parity`). Default `INFO`.

`convert`, `view` and `results` take `--log-file PATH` as well -- a per-command option
rather than a global one, because argparse only accepts the global ones *before*
the subcommand and `ada convert in out --log-file run.log` is how it gets
typed.

Every command exits `0` on success. An argparse error exits `2`, and so does
a bare command with nothing to act on — a bare invocation prints that parser's
*full* help rather than a one-line usage, but to stderr, and still exits `2`,
because a wrong invocation must not look like success to a script. An explicit
`--help` goes to stdout and exits `0`.

Every command uses the same exit codes:

| Code | Meaning |
|------|---------|
| `0`  | Success. |
| `1`  | The command line was fine, but the operation failed or was refused: an HTTP error, a declined request, an aborted prompt, a tool missing from the environment, an uncaught exception. |
| `2`  | The command line was wrong: an argparse error, a bare invocation, or a missing or invalid argument, including a missing URL, token or scope. Printed as `ada <command>: error: <what was wrong>` where the command raises it itself. |
| `3`  | `convert --strict` found an omission or a suspect finding. |

To choose between `1` and `2`: if changing the command line would fix it, it is `2`.

The command groups are `convert`, `view`, `results`, `build`, `files`, `audit`,
`serve`, `desktop` and `viewers`. The first three run entirely locally; the rest of this page notes
where a group talks to a hosted viewer instead.

## `ada convert`

Convert a model to another format. Both ends are inferred from the file
extensions, and `--from` / `--to` override that inference.

| Read (`--from`) | Extensions | Notes |
|----|----|----|
| `ifc` | `.ifc` |  |
| `step` | `.step`, `.stp` |  |
| `xml` | `.xml` | GeniE XML. |
| `gnx` | `.gnx` | GeniE workspace: the same concept XML zipped with its ACIS body, which is what GeniE opens directly. `ada.from_gnx` is the Python entry point. A body GeniE V9.3 saved in binary (`acisGeometry.sab`) reads as its text twin would; one holding a record the reader was not verified on is refused by name. |
| `acis` | `.sat`, `.acis` |  |
| `abaqus` | `.inp` | Also how you read a Calculix deck; they share the keyword syntax. |
| `sesam` | `.fem`, `.sif` |  |
| `code_aster` | `.med`, `.rmed` |  |

| Write (`--to`) | Extensions | Notes |
|----|----|----|
| `ifc` | `.ifc` |  |
| `step` | `.step`, `.stp` |  |
| `gltf` | `.gltf`, `.glb` | `.glb` is the binary flavour. |
| `xml` | `.xml` | GeniE XML. |
| `gnx` | `.gnx` | GeniE workspace. Same model as `xml`, in the container GeniE saves and opens; `Assembly.to_gnx` is the Python entry point. The ACIS body is text unless `--binary-acis` asks for binary. |
| `abaqus` | `.inp` | Default owner of `.inp`. One self-contained deck — the include files the writer uses internally are inlined and removed. |
| `calculix` | `.inp` | Shares `.inp` with Abaqus, so it is reachable only as `--to calculix`. |
| `sesam` | `.fem` | Default owner of `.fem`. The `.FEM` deck alone; `--sestra-inp` adds a `sestra.inp` made from the model's analysis step. |
| `usfos` | `.fem` | Shares `.fem` with Sesam, so it is reachable only as `--to usfos`. |
| `code_aster` | `.med` | The output is the `.med` mesh; the `.comm` command file and two `.json` maps land beside it. |
| `opencourant` | `.rad` | OpenCourant (OpenRadioss) starter deck `<name>_0000.rad`. The engine deck `<name>_0001.rad` is written beside it when the model carries an explicit step. Shell models only for now. |

Two extensions name more than one FEM format — `.inp` is both Abaqus and
Calculix, `.fem` is both Sesam and USFOS — so each has exactly one default
owner, picked to agree with what `ada.from_fem` already infers from a path:
`.inp` means Abaqus, `.fem` means Sesam. The minority dialect is reached with
`--to` and no other way. Inference is not silent about it: resolving a shared
extension is logged at `INFO`, naming the flag that would have chosen the other
one.

`xml` and `gnx` are the inverse case: one model, two containers. A `.gnx` is
the GeniE workspace -- the concept XML plus its ACIS body, zipped the way GeniE
saves one -- so the extension decides which container is written and `--to`
cannot override it. `ada convert in.xml out.gnx --to xml` is a usage error
rather than an override, because the flag selects no conversion there: it can only
put plain XML under a name GeniE will try to unzip, or a zip under a name an XML
parser will try to parse, and neither file opens. Drop the flag and let the
extension pick, or name the output with the extension you meant.

The output argument always names **one file**, never a directory, and that exact
path is what exists when the command exits `0`. This is worth stating because
the FEM writers do not work that way on their own: called through the Python API
they name the deck after the *model* and leave it in a scratch directory
(`<scratch>/<name>/<name>T1.FEM` for Sesam, `ufo_bulk.fem` for USFOS
regardless of the name given). The CLI writes into a temporary directory next to
your output and moves the format's primary deck onto the path you asked for.

Formats that genuinely need more than one file write the rest **beside** it,
under the writer's own names — so point the output at a directory of its own when
the sidecars matter. Every path written is printed to stdout as an absolute path,
one per line, the file you asked for first and the sidecars after it:

```console
$ cd /work && ada convert model.inp analysis/mesh.med
/work/analysis/mesh.med
/work/analysis/mesh.adapy_fem.json
/work/analysis/mesh.comm
/work/analysis/mesh.name_map.json
```

The sidecars are printed sorted by name, so the output is stable between runs.
The temporary directory the deck is built in goes away
even if the writer fails, so a failed conversion does not litter the output
directory; in the rare case it cannot be removed, a warning names it.

Sidecars keep the writer's own names, so converting twice into one directory
replaces the previous run's sidecars -- that is warned about, and giving each
conversion its own output directory avoids the question entirely.

Two consequences of that rule are worth knowing. A Sesam output may be named
either `model.FEM` or `modelT1.FEM` — the `T1` the Sesam writer appends is
recognised, not doubled, so a name that already has it needs no rename
afterwards. And Code_Aster output must be named `*.med`, because the mesh is
the primary file; naming the `.comm` is a usage error rather than a silent
surprise.

```bash
ada convert model.sat model.stp
ada convert model.ifc model.glb
ada convert model.gnx model.ifc                  # GeniE workspace -> IFC
ada convert model.xml model.gnx                  # concept XML -> workspace
ada convert model.xml model.gnx --binary-acis    # same, ACIS body as binary SAB (GeniE V9.3+)
ada convert model.inp model.FEM                  # Abaqus deck -> Sesam deck
ada convert model.inp analysis/modelT1.FEM       # same, named as Sesam names it
ada convert model.inp ufo/model.fem --to usfos
ada convert model.FEM ccx/model.inp --to calculix
ada convert deck.dat model.FEM --from abaqus
ada convert --list-formats
```

`-f`, `--from`
:   Read the input as this format instead of inferring it from the extension.
    Use it for an Abaqus deck that is not named `.inp`, or to read a Calculix
    deck (`--from abaqus`). There is deliberately no `--from calculix`:
    adapy has no Calculix reader, and a `.frd` is a result file, not a model.

`-t`, `--to`
:   Write this format instead of inferring it from the output extension. This is
    the only route to `calculix` and `usfos`. An explicit `--to` always
    wins; if it disagrees with the extension you named, the conversion still runs
    and logs a warning naming the usual extension.

`--list-formats`
:   Print both tables, with the primary file each FEM writer produces, and exit
    `0`. Works without the input and output arguments.

`--split`
:   Split ACIS/SAT bodies into individual faces.

`--limit`
:   Stop after this many geometries. Debugging aid; unset by default.

`--log-file [PATH]`
:   Write adapy's log records to this file, truncating it first, and leave only
    warnings and errors on the console. Given without a path, `convert` writes
    `<OUT stem>.log` beside OUT (put the flag after the positionals, or it takes
    the input file for its path). Useful on large FEM decks, where the
    `INFO` stream is worth keeping but not worth reading as it scrolls past.
    Without the flag nothing changes: every record at `--log-level` goes to
    stderr.

`--superelement`
:   The Sesam super element number, written as the deck's `IDENT` `SELTYP` and
    used to name the file `<prefix>T<N>.FEM`. Without it the `T<N>` in OUT
    decides, then what the input says (a `.SIN`'s superelement, a `.FEM`'s
    `IDENT SELTYP`), then an `R<N>`/`T<N>` in the input's name, then 1.

`--super-element`
:   Sesam `.SIN` only. Converts just this superelement (type number `12`, type
    and instance `12.1`, or Xtract's label `SEL12.IND1`), written to OUT as
    given. Repeat it to convert several, each to `<OUT stem>T<N><OUT suffix>`.
    Without it, a `.SIN` holding an assembly is written as one such file per
    first-level superelement with a mesh, each with its own `SELTYP`, and every
    file written is printed.

`--merge`
:   Sesam `.SIN` assembly only. Writes all first-level superelements as one file
    at OUT, with the top level's `SELTYP`. Each superelement is placed by its
    translation and its node and element numbers are prefixed by its type number
    (type x 10^6 + its own number), so they stay unique. A superelement placed
    with a rotation is refused, since the beam orientations and local systems
    would have to be turned with it. Cannot be combined with `--super-element`.

Without it the `T`-number in the output name decides, so `myPrefixT10.FEM`
is super element 10. With neither, it is 1 -- and the run says so, rather than
leaving you to discover it from Presel. Sesam expects the number in the deck and
the number in the file name to agree, because Presel matches them when it
assembles, so a flag contradicting the name is a usage error rather than a
silent override.

`--sestra-inp`
:   For `sesam` output: also write a Sestra control file, `sestra.inp`, beside
    the deck. adapy makes it from the model's first step (a linear static or
    eigenvalue analysis) and points it at the super element written (`ITOP`). It
    is not read from the input -- not even from a `.SIN`. By default only the
    `.FEM` is written. Any other output format refuses the flag.

`--binary-acis`
:   For `gnx` output: store the ACIS body as binary SAB (`acisGeometry.sab`),
    the way GeniE V9.3 saves a workspace with *Write ACIS files in binary format*
    on, and set that option in the workspace so a re-save in GeniE stays binary
    (`Assembly.to_gnx(..., binary_acis=True)` in Python). The default is text,
    because GeniE before V9.3 opens a binary workspace as an empty model without
    a message; V9.3 reads either. Any other output format refuses the flag.

`--strict`
:   Exit `3` if anything in the input could not be written to the output, or if
    the input itself looks wrong. Approximations on their own do not fail -- a tie
    resolved to the nearest node is always one. The deck and the conversion report
    are written either way; `--strict` reports, it does not withhold.

### What a conversion could not carry across

Every format lacks something another can say, so a conversion can be complete,
approximate, or incomplete. `ada convert` says which, in three places:

- a short summary on **stderr** at the end -- the status, then one line per
  finding. It is printed rather than logged, so `--log-file` cannot hide it,
  and it is on stderr so `ada convert in out > paths.txt` still captures
  nothing but paths;
- `<OUT stem>_conversion_report.json` beside the output, listing every finding
  with counts and measurements, and named among the written paths on stdout. It
  is written **only when there is something to report**: a clean conversion
  leaves the output file alone;
- the exit code, under `--strict`.

A finding is one of four kinds. `omitted` means the construct produced nothing
in the output -- an unsupported constraint type, a keyword the reader has no
handler for. `suspect` means the output is faithful and valid but the *input*
looks like a modelling error, so the result is faithful to a wrong model: two
constraints making one node's degree of freedom dependent is the case it exists
for, because Sesam sums linear dependencies and would quietly add them together.
`approximated` means something was written with different physics, and carries a
measure of the difference (a pairing distance, a dropped weight). `note` means
it was written faithfully and is worth a human's attention, or is plain inventory.

Omissions and suspect input fail `--strict`; approximations and notes do not.

Findings are counted per construct, not per node: a deck that drops ten thousand
springs reports one line saying ten thousand, because a log nobody can scroll
hides an omission just as well as no log at all.

## `ada view`

Open the built-in web viewer on a model or a result file.

A result file is recognised by its extension and shown with its results, every
field and step selectable in the viewer, as `ada.from_fem_res(path).show()`
would:

| Results | Extensions |
|----|----|
| `sesam` | `.sin`, `.sif` |
| `abaqus` | `.odb` (needs a local Abaqus to export it) |
| `calculix` | `.frd` |
| `code_aster` | `.rmed` |
| `opencourant` | `.radanim` |

```bash
ada view model.ifc
ada view model.ifc --renderer trimesh
ada view deck.dat --from abaqus
ada view run.SIN                                 # the results
ada view run.SIN --super-element SEL12.IND1      # one superelement of the assembly
ada view run.rmed --from code_aster              # the mesh alone
```

`-f`, `--from`
:   Read the input as this model format instead of inferring it from the
    extension. Same names as `ada convert`'s `--from`. On a result file it
    reads the mesh alone, without the results.

`--renderer`
:   `react` (default) or `trimesh`. Results are shown with `react`.

`--host`
:   Host to bind the viewer websocket to. Default `localhost`.

`--ws-port`
:   Websocket port. Default `8765`.

`--split`, `--limit`, `--log-file`
:   As for `ada convert`.

`--super-element`
:   Sesam `.SIN` only. A superelement assembly is read whole by default, every
    superelement placed and merged; this reads one superelement instead, given as
    a type number (`12`), type and instance (`12.1`) or Xtract's label
    (`SEL12.IND1`). Repeat the flag to read several, merged like the whole
    assembly.

`--stats`
:   Result files only: print the overview `ada results stats` prints before
    showing the result.

`--viewer`
:   Open an installed viewer bundle instead of the built-in viewer (see
    `ada viewers`). Default: `$ADA_VIEWER_BUNDLE`, else the built-in viewer.

## `ada results`

Read FEA result files -- the extensions `ada view` shows as results -- without
opening a model. Both subcommands take `--super-element` and `--log-file`
as `ada view` does.

`ada results stats`
:   Print an overview of one result file: the software, the steps with their
    names, the mesh contents and one row per field (kind, steps, components,
    shape).

`ada results compare`
:   Compare `OTHER` against `BASE`, two runs of the same model with different
    loads or boundary conditions, and print the fields that differ, worst first.
    Then show the difference `OTHER - BASE` in the viewer: one field per compared
    field, for every load case and combination, each coloured on a scale centred
    on zero, so a model compared with itself is one colour. The two must share a
    mesh -- node and element ids, coordinates and connectivity -- or the command
    exits `2`; `--no-check-mesh` compares anyway. `--fields` limits it to the
    named fields, `--rtol` sets the relative difference above which a field
    counts as differing (default `1e-5`), `--max-rows` caps the table (default
    50, `0` for all), and `--no-show` stops after printing. `--renderer`
    (`react`), `--host` and `--ws-port` are as for `ada view`.

```bash
ada results stats run.SIN
ada results compare base.SIN other.SIN
ada results compare base.SIN other.SIN --fields sesam.nodes.displacement --no-show
```

## `ada build`

Run the entrypoints declared in an `ada_config.toml` and push the artefacts
they produce to a viewer. The three subcommands share `--config` (default
`ada_config.toml`), `--entrypoint` (run only the named one) and
`--output-dir` (default `.ada-build`).

`ada build run`
:   Run the entrypoints and stage the artefacts locally.

`ada build upload`
:   Upload the artefacts already under the output dir.

`ada build run-and-upload`
:   Chain the two. This is the one to use in CI.

Uploading needs a target and a credential, read from `ADAPY_VIEWER_URL` and
`ADAPY_VIEWER_TOKEN` (the newer `ADAPY_API_BASE` / `ADAPY_API_TOKEN` pair
is accepted too, and wins when both are set). A `.env` in the working
directory is picked up automatically; real environment variables win over it.

## `ada files`

List and move blobs in a viewer scope. Every subcommand takes `--url`,
`--token` and `--scope`, each defaulting to the matching environment
variable. A scope looks like `project:my-slug` or `user:me`.

`ada files list`
:   List keys in the scope. `--prefix` filters, `-l`/`--long` adds sizes.

`ada files download`
:   Download `KEY` to `DEST` (default: its basename in the working
    directory). Goes S3-direct through a presigned URL where the backend
    supports it; `--via-api` forces the tunneled GET instead.

`ada files upload`
:   Upload `SRC` to `KEY` (default: the basename of `SRC`). `--via-api`
    forces the tunneled PUT, which is subject to the direct-upload size cap.

`ada files delete`
:   Delete the given keys, and/or everything under `--prefix`. `-y`/`--yes`
    skips the confirmation prompt.

```bash
ada files list --scope project:my-slug -l
ada files upload model.glb versions/main/abc1234/model.glb
ada files delete --prefix debug/ --yes
```

## `ada audit`

A read-only client over the viewer's audit API, plus two local re-run paths.
Credentials come from `ADAPY_API_TOKEN` with the base URL from
`ADAPY_API_BASE` or `ADAPY_BASE_URL`; a bare host is accepted and gets
`https://` prepended. Every subcommand accepts `--url`, `--token` and
`--json` (raw JSON instead of a table).

`ada audit runs`
:   List recent regression-sweep runs. `--limit` (default 20) and `--before`
    page through them.

`ada audit run`
:   Show one run's per-cell jobs. `--failed` narrows to failures, `--format`
    to a single target format.

`ada audit log`
:   Query the per-conversion audit log, filtered by `--source`, `--target`,
    `--status`, `--key` or `--grep`, and server-side by `--action`
    (`convert`, `view`, `render`, `validate`, …) and `--since` /
    `--until` (a relative duration such as `6h` or an ISO-8601 instant).

`ada audit loads`
:   Per-load browser model-load metrics recorded by the viewer's opt-in
    instrumentation: transport, TTFB, download, parse, prepare and first-render
    times, bytes and triangles. Filter with `--since`, `--until`, `--key`
    and `--device` (a device-id prefix); `--kind render` lists steady-state
    render windows instead. A row is written only once a load completes;
    `transport=relayed` means the server relayed the bytes instead of a direct
    storage fetch, with the reason in `client_metrics.fallback_reason`; and
    `first_render_ms` pauses while the tab is hidden.

`ada audit loads-summary`
:   Per-file p50/p95 load times over `--since-days` (default 1), split into
    network, CPU and GPU time with the dominant bottleneck. `--kind render`
    summarizes render windows (FPS, frame and GPU time).

`ada audit loads-hotspots`
:   Function-level self-time across profiled browser loads (or render windows
    with `--kind render`), optionally for one `--key`.

`ada audit perf`
:   Hot paths across conversions — function-level by default, cell-level when
    `--run`, `--worker-tag` or `--trigger` is given.

`ada audit profile`
:   Function stats for a single audit row's cProfile. `--sort` takes
    `cumtime` (default), `tottime` or `ncalls`.

`ada audit fetch`
:   Download one conversion's source blob under `--out`
    (default `./audit_repro`).

`ada audit logfile`
:   Download a conversion's captured stdout/stderr. Prints to stdout unless
    `--out` names a file.

`ada audit repro`
:   Fetch a conversion's source and run it locally, optionally against a
    different `--target` format.

`ada audit wasm-sweep`
:   Re-run a remote run's cells locally through the in-browser WASM engine
    (node-pyodide) and write a pass/fail report. Writes nothing back to the
    database.

`ada audit parity`
:   Cross-format visual-parity check on a local model: export to each of
    `--formats` (default `ifc,xml,step`), reload, and compare the visualized
    element counts.

```bash
ada audit runs --limit 5
ada audit run 42 --failed
ada audit repro 1234 --target step
ada audit loads --since 2h --device 3f2a9c1e
ada audit loads-summary --since-days 7
```

## `ada serve`

Run one of the long-lived server processes. Both are configured through the
environment; `api` also takes `--viewer`.

`ada serve api`
:   Run the REST API under uvicorn. `--viewer NAME` serves an installed viewer
    bundle instead of the built-in viewer (see `ada viewers`); an explicit
    `ADA_VIEWER_STATIC_PATH` still wins.

`ada serve worker`
:   Run the conversion worker (a NATS JetStream consumer).

## `ada desktop`

Run the local viewer and open it in a native window instead of a browser tab.
It starts `python -m ada.comms.rest` with local storage, no login and no job
queue, bound to 127.0.0.1, opens the `ada-desktop` window on it, and stops it
when the last window closes. Pop-outs and dashboard windows open as native
windows. The window shell (`ada-desktop`) comes in a separate conda package,
`ada-py-desktop` (Windows only for now);
see `src/desktop/README.md` in the repository for how it is built and what it
allows the page to do.

```bash
ada desktop                      # the viewer, empty
ada desktop model.ifc            # upload model.ifc into the personal scope and open it
ada desktop --no-window          # the viewer only; open the printed URL in a browser
```

`FILE`
:   Optional. Uploaded into the local viewer's personal scope under its file
    name and opened with `?file=`, as if uploaded from the page. A type the
    viewer cannot read is reported and the window opens without it.

`--port N`
:   The viewer port. Default: `$ADA_DESKTOP_PORT`, else a fixed port for the
    current user in 38500-38999 (`ada desktop --help` shows yours). It is
    fixed on purpose: the viewer keeps layouts and settings in browser storage,
    which belongs to the page's origin and so to the port. If another program
    holds the port, this launch uses a free one and warns that it starts
    without those settings. If a viewer started by another `ada desktop`
    already runs there, the command opens one more window on it instead, and
    that viewer stops only when every window is closed.

`--data DIR`
:   Local storage (uploaded files, derived results). Default: a per-user
    directory, `%LOCALAPPDATA%\ada\desktop\data` on Windows.

`--viewer NAME`
:   Serve an installed viewer bundle (see `ada viewers`) instead of the
    built-in viewer.

`--static DIR` / `--zip FILE`
:   Serve a built viewer directory, or a single-file viewer zip. The default
    is the zip shipped in ada-py, unpacked once per content hash into the user
    cache directory.

`--binary PATH`
:   The `ada-desktop` shell. Default: `$ADA_DESKTOP_BINARY`, else
    `ada-desktop` on `PATH` or in the active environment (`Library\bin` on
    Windows). Without one the command says how to install it.

`--title T`
:   Main window title (default: Ada Studio).

`--no-window`
:   Start the viewer only, print its URL and wait for Ctrl+C.

`--shell-arg ARG`
:   Pass `ARG` to the shell, e.g. `--shell-arg=--no-spacemouse`. Repeatable.

`--timing`
:   Print how long the viewer took to start and stop.

`--dev`
:   For working on adapy itself (a source checkout, Windows only for now;
    `pixi run desktop` does this and builds what it needs). The viewer runs
    under `uvicorn --reload` (Python edits under `src/ada` restart it), the
    frontend runs under Vite with hot module reload, and the window opens on
    Vite, which proxies `/api`, `/config.js` and `/healthz` to the viewer. The
    shell is not reloaded. Vite listens on the viewer port + 500 unless
    `--dev-port N` says otherwise.

`--frontend-url URL`
:   Implies `--dev`, but opens an already running Vite dev server (loopback
    `http` only) instead of starting one. Start it with
    `ADA_DESKTOP_BACKEND=http://127.0.0.1:PORT/` and
    `--config vite.config.desktop.ts`.

### Connecting to a hosted server

`ada desktop` can open a hosted (for example on-premise) deployment instead of
the local viewer, to use its storage, conversions, jobs and sign-in. No local
viewer is started; the window loads the server's own URL, so the page runs on
the server's origin and its normal sign-in works unchanged.

```bash
ada desktop --server https://studio.example.com
ada desktop servers add work https://studio.example.com
ada desktop --server work
ada desktop servers list
ada desktop servers remove work
```

`--server URL|NAME`
:   The deployment to open: an `https` URL, or the name of a saved server.
    Default: `$ADA_DESKTOP_SERVER`. The origin the window will trust is printed
    first. `FILE` and the local-viewer flags (`--port`, `--data`, `--static`,
    `--zip`, `--viewer`) cannot be combined with it. With `--dev` it means
    `--backend`.

`--local`
:   Start the local viewer even though `$ADA_DESKTOP_SERVER` is set.

`--insecure`
:   Accept plain `http` for a server on a non-loopback host (lab setups only;
    sign-in tokens then cross the network unencrypted). https is required
    otherwise.

`--auth-origin ORIGIN`
:   An extra identity-provider origin the window may sign in at. The provider
    the server advertises (`/api/config`) is allowed automatically; add the
    others a federated sign-in passes through. Repeatable; `servers add` saves
    them with the profile.

`servers list | add NAME URL | remove NAME`
:   Manage saved servers, kept in `%APPDATA%\ada\desktop\servers.json` on
    Windows (`$XDG_CONFIG_HOME/ada/desktop/servers.json` on Linux).

`--backend URL|NAME`
:   Implies `--dev`, against a hosted server (a URL or a saved name) instead of
    a local viewer (source checkout, Windows; `pixi run desktop-backend NAME`).
    Vite serves the local frontend with hot reload on
    `http://localhost:PORT` (the `--dev` port, viewer port + 500, fixed per
    user; `--dev-port N` overrides) and proxies `/api` (websockets included),
    `/config.js` and `/healthz` to the server. The command prints
    `Register this redirect URI for the app's SPA platform:
    http://localhost:PORT/auth/callback`; register it once in the server's
    identity provider. `--dev --server NAME` is the same; a plain `--dev`
    stays local. `pixi run desktop-server NAME` just opens the server.

See `src/desktop/README.md` for the security model (which origins may be shown
and which one is granted native events) and the sign-in details.

## `ada dev`

Develop against the studio, with what you edit reloaded live.

`ada dev connections`
:   Develop a connection library: the joint details registered with
    `@register_connection`. Starts the local viewer (as `ada desktop` does)
    with the library in `--dir` loaded and opens the studio's connection
    builder. Pick a spec from the library, then edit each member: a beam's
    section, a plate's thickness and extent, the angle between them. The joint
    is rebuilt on every change, and again on every saved edit to the library's
    files. A builder that raises, or a module that does not import, shows its
    traceback in the builder until the next save fixes it.

`ada dev joints`
:   The same as `ada dev connections`.

The library declares itself in `ada_connections.toml` in `--dir` (default
`.`):

```toml
[library]
name = "my-connections"               # shown in the studio (default: the folder name)
package = "my_project.connections"    # imported, with every submodule, to register the specs
# loader = "loader.py:load_all"       # ...or a function that does the importing itself
watch = ["."]                         # what a saved change reloads (default: this folder)
python_path = ["../.."]               # prepended to sys.path, for a library that is not installed
# handler_kwargs = "dev.py:preview_kwargs"  # a function returning extra keyword arguments for the builders
```

Paths are relative to the toml. The library runs in a process of its own
next to the viewer. A reload drops the library's own modules and specs and
imports them again, so the heavy imports stay loaded and a reload takes well
under a second; editing `ada_connections.toml` starts a fresh process. The
preview members come from the spec's roles: a landing beam runs along X
through the joint, an incoming beam starts there at the role's angle, and a
plate stands on the landing member's top face. A spec's `defaults` fill the
form; fields the spec leaves out take a representative value.

Options: `--port N` (default: your `ada desktop` port + 1000, so the two run
side by side), `--data DIR`, `--static DIR` / `--zip FILE` / `--viewer NAME`
(the viewer build to serve, as for `ada desktop`), `--browser` (open the
default browser rather than the `ada-desktop` window, which is also the
fallback when the shell is not installed), `--no-window`, `--binary PATH`,
`--title`, and `--dev` (run the viewer from the source checkout with
hot reload, as `ada desktop --dev`).

With welds, the detail can be simulated: the Connection tab's **Simulate...**
button offers it as a *weld assembly* subject and opens the Simulations panel,
where the providers that accept one can run on it. The studio has no simulation
workers of its own, so point it at an Ada Studio that has them with
`--sim-remote URL` (and `--sim-token TOKEN`, or `ADA_SIMULATION_REMOTE_TOKEN`;
`--sim-scope SCOPE` for the remote scope the run lives in, default `user:me`):
the remote's providers are listed beside the local ones, and a run on one
uploads its files to the remote, starts there, and is followed from here (the
replay, live view and console read it through this studio).

```
ada dev connections --dir path/to/my_project/connections
ada dev connections --dir path/to/connections --sim-remote https://studio.example.com --sim-token $TOKEN
```

## `ada viewers`

List the installed viewer bundles. adapy's own viewer carries no plugins; they
join its frontend at build time. A package that ships plugins can build the
viewer with them and install the result as a bundle, so a local viewer can use
it without building anything:

```
<prefix>/share/ada/viewers/<name>/
    bundle.json   name, title, adapy_version, plugins, default_ui
    dist/         the hosted build, served by `ada serve api`
    index.zip     optional: the single-file build `ada view` and show() open
```

`--viewer NAME` on `ada view` and `ada serve api` (or `ADA_VIEWER_BUNDLE=NAME`)
picks one; unset, or `builtin`, is the built-in viewer. `ADA_VIEWER_BUNDLE_PATH`
adds directories to look in. An unknown name is an error naming what is
installed.

```bash
ada viewers
ada serve api --viewer asa
```
