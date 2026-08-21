# UI rebuild — M0 baseline

Measured on `feat/gui_updates` at the M0 groundwork commit, before any shell or
design-system work. Every later milestone is compared against these numbers.

Regenerate the audit figures with `npm run ui:audit`.

## Build sizes — the budget

| Build | Command | Artefact | Baseline |
|---|---|---|---|
| Desktop / pip (single chunk) | `npm run build` | `dist/index.html` | **2,826,306 B** |
| Hosted (chunk-split) | `npm run build:serve` | `dist/assets/*.js` | 12+ chunks emitted |
| Embed (paradoc / Jupyter) | `npm run build:embed` | `dist-embed/index.js` | **3,304,986 B** |

**Budget for the whole rewrite: +8 % max on `dist/index.html`.** The expected end
state is net-negative, because M8 deletes `Menu.tsx`, `EmbedUI.tsx`,
`SimWindowFrame.tsx`, `InViewerPanelHost.tsx` and one of the two drag
implementations.

Contract checks that must keep passing:
- `dist/index.html` contains **no** `<script src="/assets/*.js">` (single-chunk
  contract — the only `src` is `/config.js`, injected at serve time).
- `build:serve` still emits separate three / react / xyflow chunks.
- `dist-embed/` contains only `index.js` + `index.d.ts`, no `.css` sidecar, and
  **zero** `process.env` references.

## Design-system burn-down

From `npm run ui:audit` (full row-level report in the gitignored `docs/ui-audit.csv`;
the committed `docs/ui-audit.summary.json` is the diffable metric).

| Metric | M0 |
|---|---|
| Files scanned | 355 |
| `className` strings | 2,125 |
| Distinct class recipes | 1,244 |
| Files hardcoding `bg-*` colours | 83 |
| Raw `<button>` | 375 |
| Raw `<input>` | 115 |
| Raw checkboxes | 60 |
| Raw `<select>` | 77 |
| Raw `<textarea>` | 2 |

Heaviest files by class-string count: `AuditLogTab` 156, `CellBuilderPanel` 117,
`AuditRunsTab` 110, `CorpusTab` 104, `FrontendLoadsTab` 87, `ConversionSettingsTab`
78, `StorageBrowser` 69, `StorageTab` 67.

Top codemod targets (call sites / files): `flex items-center gap-1` 49/9 ·
`flex flex-col gap-0.5` 25/7 · `flex items-center gap-2` 23/15 ·
`text-xs text-gray-400` 19/10 · the `px-2 py-1 rounded-sm bg-blue-600 …` Button
family 8/8.

## Tests

`npm test` — **218 pass, 0 fail, 0 skipped.** (Was 217/0/1: the
`detailingPanel.smoke.test.tsx` client-render test skipped because jsdom was a
"throwaway diagnostic dep". jsdom is now a declared devDependency and it runs.)

## Keyboard shortcuts — the parity contract

Captured from `src/components/viewer/sceneHelpers/setupCameraControlsHandlers.ts`.
All are Shift-modified and all are suppressed while focus is in an
`<input>`/`<textarea>`/`contentEditable`. After the rewrite these must still fire
from the canvas and must still **not** fire from a dock text input.

| Keys | Action | Source line |
|---|---|---|
| `Shift+H` | Hide selection | `:75` |
| `Shift+U` | Unhide all | `:77` |
| `Shift+F` | Centre on selection | `:79` |
| `Shift+A` | Zoom to fit | `:90` |
| `Shift+Enter` | Preview-compile (cellbuilder) | `:100` |
| `Shift+Q` | Toggle Options drawer | `:113` |
| `Shift+T` | Toggle selection tree | `:116` |
| `Shift+C` | Copy selected names | `:119` |
| `Shift+↑` | Tree: parent | `:126` |
| `Shift+↓` | Tree: child | `:134` |
| `Shift+←` | Tree: previous sibling | `:137` |
| `Shift+→` | Tree: next sibling | `:140` |

Tool-scoped keys (cellbuilder only, active when `cellBuilderStore.active !== null`,
handled in `CellBuilderController.tsx` on the capture phase): `G`/`R`/`S` gizmo
mode · `X`/`Y`/`Z` axis lock · `Shift+H`/`Shift+U` hide/unhide cells ·
`PageUp`/`PageDown` equipment floor · `Ctrl/⌘+Z`, `Shift+Z`, `Y` undo/redo ·
`Esc` step back a layer · `Enter` apply gizmo value.

Gallery mode: `←`/`→` previous / next item.

> These are **tool**-scoped, not **mode**-scoped. The rewrite must not re-key them
> to the new mode system — see the non-modality contract in the plan.

## Reviewing locally

```
npm run dev                 # http://localhost:5173  (WS/desktop mode, no backend)
npm run dev -- --open       # …and open a browser
#   ?demo=1                 load the committed fixture model (public/dev/demo.glb)
#   ?demo=<name>            load public/dev/<name>.glb instead

npm run dev:rest            # same, but /config.js sets COMMS_MODE=rest
#   ADA_DEV_API_BASE=...    point at a backend (default http://localhost:8000/api)
#   run the backend with:   pixi run -e viewer-api viewer-api

npm run build:embed && npm run embed:dev
#   then open http://localhost:5180/embed/dev.html  — the embed harness
```

The fixture does **not** auto-frame on load (`optionsStore.autoFit` is off by
default). Press `Shift+A`. Regenerate the fixture with `npm run fixture`.

## Defects found while establishing the baseline

Three pre-existing breakages, all fixed in M0 because the review workflow depends
on them. None were introduced by the rewrite.

1. **`npm run dev` did not start at all.** esbuild is pinned to 0.28.1 (security fix,
   `package.json` overrides) and cannot lower destructuring; dependency pre-bundling
   used the default browser-list target and failed with 417 errors on
   `@xyflow/react` and `@tanstack/react-virtual`. `build.target: 'esnext'` was
   already set for exactly this reason but does not apply to the dev pre-bundle.
   Fixed by adding `optimizeDeps.esbuildOptions.target: 'esnext'`.

2. **`npm run build:embed` had been broken since 2026-06-17.** `embed/stubs/pyodide_converter.ts`
   (last touched 2026-06-03) stopped exporting everything its consumer imports when
   PR #225 added the streaming entry points. Nothing exercised the embed build, so
   it stayed broken for two months. Fixed by syncing the stub; `embed/dev.html` now
   exercises it.

3. **The embed bundle threw `process is not defined` on import.** Vite lib-mode
   builds do not substitute `process.env`, so 64 of React's `process.env.NODE_ENV`
   guards shipped into a bundle documented as self-contained ESM. Fixed with an
   explicit `define`; as a side effect React now runs in production mode there and
   the bundle shrank 3,917,720 → 3,304,986 B (−16 %).

---

# M1 — design-system kernel

Review it at **`npm run dev` → http://localhost:5173/?uikit=1`**. The real app is
untouched by this milestone; the gallery is a separate route that mounts before every
other branch and needs no comms, scene or auth.

## What landed

| | |
|---|---|
| `src/ui/themeTokens.ts` | Derives the full colour set from the user's panel theme. DOM-free, so it unit-tests under plain `node --test`. |
| `src/ui/tokens.css` | Static tokens (radii, spacing, control heights, type, icons, elevation, motion, z-index) + the Tailwind v4 `@theme` namespace. |
| `src/ui/selectionColor.ts` | One definition of the selection colour, imported by both the three.js material and `--ada-select`. |
| `src/components/ui/*` | 17 primitives + barrel. |
| `src/components/icons/index.tsx` | Name→component registry (50 glyphs) and the `<Icon>` wrapper. |
| `src/components/ui/__gallery__` | The live catalogue at `?uikit=1`. |

## Sizes after M1

| Build | M0 | M1 | Delta |
|---|---|---|---|
| `dist/index.html` | 2,826,306 B | 2,873,391 B | **+1.67 %** (budget +8 %) |
| `dist-embed/index.js` | 3,304,986 B | 3,324,446 B | +0.59 % |
| `build:serve` chunks | 12+ | 29 | — |

Contract checks still hold: zero `/assets/*.js` script tags in `dist/index.html`,
zero `process.env` in the embed, no `.css` sidecar.

**Tests: 260 pass, 0 fail, 0 skipped** (M0: 218). New suites — `ui/tokens`,
`ui/embedCssHoist`, `ui/noAdHocChrome`, `ui/primitives.smoke`.

## Burn-down: unchanged, by design

M1 built the system; it converted no consumers. The audit numbers move from M3
onward, as panels are re-chromed. `src/__tests__/ui/adHocChrome.allowlist.json`
holds the 81 files under `src/components/**` still naming palette colours; the test
fails both on a new offender and on a stale entry, so the list can only shrink.

## Two things worth knowing

**Selection colour changed.** It was the CSS keyword `blue` (#0000FF), which is so
dark it read as a hole in the geometry rather than a highlight, and failed contrast
against the dark panel presets. It is now #2563EB, defined once in
`ui/selectionColor.ts` and consumed by the three.js material *and* `--ada-select`, so
a selected outliner row and the selected geometry cannot drift apart.

**Panel presets were designed as translucent overlays over 3D, not as app chrome.**
Viewed full-page in the gallery, `Pale glass` (50 %-alpha grey, white text) is
low-contrast, because there is no bright 3D content behind it. `Slate glass`, `Dark`
and `Mist` all read well. When M2 introduces docked regions this needs a decision:
either docked panels use an opaque surface, or the pale preset is revised. Flagged,
not silently changed — the presets are a user-facing choice.

---

# M2 — the shell

Review it at **`http://localhost:5173/?shell=1&demo=1`**. The choice is remembered, so
later reloads need no query string; `?shell=0` (or the ⇱ button in the title bar) returns
to the classic UI, which stays the default and is untouched by this milestone.

## The layout

```
titlebar   titlebar   titlebar   titlebar   titlebar
rail       leftdock   split-l    viewport   rightdock
rail       bottomdock bottomdock bottomdock bottomdock
statusbar  statusbar  statusbar  statusbar  statusbar
```

A CSS grid whose track sizes come from `layoutStore`. **This is the whole mechanism for
the headline fix**: dragging a splitter changes one number, the grid reflows,
ThreeCanvas's existing `ResizeObserver` fires and three.js resizes itself. The viewport
is a *track*, not a backdrop, so a panel cannot cover the model. No renderer change was
needed — the observer was already there, waiting for a container that actually changed
size.

The bottom dock spans the full width deliberately: the FEA data table and the conversion
log are wide-and-short, and putting them across the bottom is what stops them being
floated over the geometry.

**No docking library.** Two reasons, and the second is decisive: the pip-bundled desktop
build inlines everything into one HTML file, so a ~90 KB dock library lands whole in it;
and every docking library re-parents DOM nodes when you drag a tab, which would orphan
the imperatively-appended WebGL canvas. `react-rnd` (already a dependency) is used only
for the float layer.

## Verified in the browser

- Splitter drag resizes the dock and the canvas reflows — model stays fully visible and
  undistorted, camera untouched.
- Mode switch Inspect → Results → Inspect: rail tools swap, docks change, and the model
  and camera are **identical** either side. Per-mode layouts restore, including a width
  set by hand.
- Layout persists across a full page navigation.
- Selection works end to end: click geometry → highlight → Properties fills → status bar
  count.
- `Shift+T` typed into the Outliner's search field does **not** toggle the dock; the
  global shortcut is correctly suppressed while focus is in a dock input.

## Sizes after M2

| Build | M0 | M2 | Delta |
|---|---|---|---|
| `dist/index.html` | 2,826,306 B | 2,896,392 B | **+2.48 %** (budget +8 %) |
| `dist-embed/index.js` | 3,304,986 B | 3,325,519 B | +0.62 % |
| `build:serve` chunks | 12+ | 31 | — |

**Tests: 303 pass, 0 fail, 0 skipped** (M1: 260). New suites — `shell/panelRegistry`,
`shell/modeSemantics`, `shell/layoutStore`, `shell/zIndex`, `shell/regionCompat`.

## Two bugs this milestone found in M1's work

**The splitter could not be focused by clicking.** `preventDefault()` in `onPointerDown`
(there to stop text selection during a drag) also suppressed the default focus, so the
keyboard resize was unreachable for pointer users. Now focuses explicitly.

**`Shift+Arrow` collided with an existing global shortcut.** The splitter used Shift for
coarse steps, but `Shift+Arrow` is already bound to tree navigation, and that handler
only skips inputs/textareas/contentEditable — a focused separator is none of those, so
one keystroke would have resized the dock *and* jumped the tree selection. Coarse step is
now Ctrl/Cmd, and the splitter stops propagation on keys it handles.

**And one M1 regression in the classic UI.** Normalising the icons stripped their
intrinsic `width`/`height` so the `<Icon>` wrapper could own sizing — which broke every
call site that renders an icon component *directly*. The classic `Menu.tsx` does exactly
that, and six of its eight toolbar icons collapsed to 0×0. Intrinsic sizes are restored;
`<Icon>` still wins because it sizes via `[&>svg]:w-full` and CSS beats presentation
attributes. Covered by a regression test.

## Deliberately not done yet

- Panels are mounted **as-is** inside the docks, so they still draw their own headers —
  a box-in-a-box in places. Re-chroming is each mode's own milestone (M3–M6).
- The tool rail's mode tools are rendered **disabled** with an honest "not wired up yet"
  tooltip. Showing the shape of each mode beats an empty rail; a live control that did
  nothing would be worse than both.
- `PanelFrame` (promoted from `SimWindowFrame`) is still pending — the float layer
  currently hand-rolls its header. It folds in with M4, where `SimWindowFrame` is deleted.

---

# M3 — Inspect mode and the unified Properties panel

## The Properties registry

`components/properties/` replaces "N bespoke info boxes, each deciding for itself
whether to appear". A provider declares **when** it applies and **what** it renders;
the panel composes whatever matches, in order.

The three panels that previously composed `ObjectInfoBoxComponent` by hard reference
are now sibling providers:

| Provider | order | applies when |
|---|---|---|
| `selection-summary` | 0 | anything selected, **or** nothing selected but the scene holds entities |
| `object-metadata` | 10 | a named mesh selection |
| `cellbuilder-cell` | 20 | a builder-cell selection |

That second half of the `selection-summary` rule is a real decision, not a detail:
scene-wide recovery (Unhide all / Fit all) must stay reachable **after you hide your
last selection**, or you have hidden something with no way to get it back.

Orders leave gaps of ten so a plugin can slot between core entries without renumbering.
This is also the genuine replacement for the plugin framework's `scene-info` region,
declared in Phase 1 and never wired.

**The split was two-phase, per the hard rule.** `ObjectInfoBoxComponent`'s 436 lines
moved into `SelectionSummary.tsx` **verbatim** — only the outer chrome was removed —
and only then was the chrome re-done. The cell-vs-mesh dispatch in that file is subtle
and correct; it was moved, not rewritten. `ObjectInfoBoxComponent` survives as a thin
wrapper so the **classic UI renders exactly what it did before**.

`match` predicates live in their own module (`coreProviderRules.ts`) because the render
half transitively imports the whole viewer — `cellBuilderStore` reaches a vite
`?worker&inline` module only a bundler can resolve. Splitting them means the
composition rules are unit-testable under plain `node --test`, which is the half most
likely to be got wrong.

## Also in M3

- **Scene panel tabs re-chromed** onto the design-system `Tabs`. The store still owns
  which tab is active; the FEM/Joints contextual dot is now the primitive's `contextual`
  flag. Gained for free: roving-tabindex arrow-key navigation the hand-rolled strip
  never had.
- **The Inspect rail is live.** Fit all, Focus selection, Hide, Unhide all and Section
  planes all work — and all **delegate** to the handlers the keyboard shortcuts and the
  classic panel already call (`hideSelectedRanges`, `unhideAllRanges`,
  `centerViewOnSelection`, `zoomToAll`, `frameCells`). Nothing is reimplemented, so the
  rail and the shortcuts cannot diverge. Section planes opens the Scene panel on its
  Clip tab rather than duplicating that UI. Measure remains honestly disabled.

## Verified in the browser

Selecting geometry fills Properties through the provider chain (`selection-summary` +
`object-metadata`); an empty selection with a model loaded still offers Unhide all; the
Section-planes rail button opens the Scene dock *and* selects its Clip tab; the Scene
tabs render FEM/Joints only when their data exists.

## Numbers

| | M2 | M3 |
|---|---|---|
| Tests | 303 | **312** (0 skipped) |
| `dist/index.html` | 2,896,392 B | 2,897,873 B (**+2.53 %** vs M0, budget +8 %) |
| Files hardcoding palette colours | 81 | **80** |

That last row is the first burn-down tick — and it came from the enforcement test
failing, correctly, when `SelectionSummary.tsx` arrived carrying colours moved out of a
file that *was* on the allowlist. The list can only shrink, so moving debt around does
not pass.

## Known limits at this point

- **The `?demo=1` fixture does not register a model *source***, so the Scene panel's
  "Loaded models" list is empty even with geometry on screen. A fixture limitation, not
  a shell defect — but it means that inventory row cannot be marked verified from the
  fixture alone.
- **Preferences still renders its own translucent chrome** inside the float panel, so
  it reads as a box in a box. It is re-chromed with its own milestone.
- **Mode-independence of Properties is verified by test, not by hand**: the fixture has
  no cellbuilder model, so "click a cell while in Results mode" needs a procedural model
  to confirm in the browser.

---

# M4 — Results mode

Review it at **`npm run dev:rest`** → `http://localhost:5173/?shell=1&fea=1`, then pick
**Results**. Needs `dev:rest`, not `dev` — the FEA loader is REST-gated.

## A real FEA fixture, and an offline REST slice to serve it

`scripts/make-fea-fixture.py` bakes the repo's own code_aster **eigen shell cantilever**
into `public/dev/fea/` — 403 points, 360 cells, **20 modes**, 6 components, 218 kB.
Eigen rather than static on purpose: several modes are what make the step scrubber worth
looking at, where a static result gives one step and proves nothing.

Serving it needed a decision. `load_fea_streaming` is REST-gated and builds its own
fetcher, so the options were to relax that gate (a fenced business-logic file) or to
stand up the URLs it already expects. The dev-rest vite plugin now serves a small slice
of the REST API — `/me`, the file list, and blob reads **with real 206 range responses**
— at exactly the paths `makeViewerApiFetcher` constructs. The review therefore exercises
the production path, per-step range requests included, rather than a shortcut around it.

The fixture loader waits for `sceneRef` before dispatching. It fires at module-eval,
before `ThreeCanvas` mounts, and without the wait it threw `scene not ready` and silently
never appeared — the same polling shape `useUrlParamLoad` uses, for the same reason.

## The bottom dock earns its keep

`fea-table` (the result data table) is registered as a **bottom-dock** panel. This is the
concrete answer to "panels cover the 3D": the table is wide and short — dozens of columns,
a handful of rows in view — so floated over the model it hides exactly the geometry you
are reading it against. Across the bottom it costs height the 3D does not need, and the
canvas reflows above it.

It ships **collapsed**: defaulting it open would spend 220 px of viewport on an empty grid
for every user who only wants to look at a mode shape. The rail button opens it.

`OverlayLayer` now carries canvas-anchored HUDs (the colour legend) *inside the viewport
track*, so a legend cannot drift over a dock when the layout changes — the failure mode of
the old `absolute right-5 top-80`, which was measured against the window.

## Two real bugs found

**Toggling a panel in a collapsed dock removed it instead of revealing it.** Results ships
`fea-table` present-but-collapsed, so `togglePanel` counted it as open: the first rail
click silently dropped a panel the user could not see, and only a second brought it back.
Fixed and covered by two regression tests.

**The shell dropped plugin `top-panel` contributions entirely.** Those slots are hosted
only in the classic `Menu.tsx`, which the shell never renders — so enabling the shell made
a plugin's top-bar button vanish. That is inventory row B11, and precisely the quiet loss
the parity checklist exists to catch. `TitleBar` now hosts them, with a test asserting
each live region has a host in `src/shell`. (`fem-sidebar` was fine: its host is
`SimulationControls`, which the shell mounts as the Results panel.)

## Numbers

| | M3 | M4 |
|---|---|---|
| Tests | 312 | **315** (0 skipped) |
| `dist/index.html` | 2,897,873 B | 2,899,394 B (**+2.59 %** vs M0, budget +8 %) |

## Deliberately deferred, with reasons

**`SimWindowFrame` is NOT deleted.** The plan had it promoted to `PanelFrame` here, but
that reasoning does not survive contact: in the shell the *dock* provides the frame, so
`SimulationControls` needs no frame of its own; in the classic UI it still does. Deleting
it belongs at M8 cutover, when the classic UI goes, not now — promoting it early would
mean maintaining two framing paths for no user-visible gain.

**`?simfollow=` still uses its own route** rather than `profile="window"`. The pop-out
follower works today; re-routing it is churn that buys nothing until the classic UI is
removed, and it carries real risk around the `ada-sim` BroadcastChannel sync.

**Playback has no rail button.** It lives on the Simulation transport, beside the step and
mode sliders it belongs with. A duplicate play control would be a second control for one
piece of state — the thing this rebuild removes, not adds.

---

# M5 — Build mode

Review it at **`http://localhost:5173/?shell=1&build=1`** → **Build**. Works in plain
`npm run dev`; unlike the FEA fixture, nothing here needs REST.

## A procedural fixture

`public/dev/procedural.json` is a hand-written `ProceduralDoc`: 4 cells across two
groups, 3 pieces of equipment, and 2 systems connecting them. Hand-written rather than
exported from a real plant model because the shape is plain JSON and a small readable one
exercises the parts that matter — grouped cells, equipment with ports, systems — without
dragging megabytes into the repo.

`?build=1` calls `cellBuilderStore.open()`, the same action the REST load path uses once
it has a doc, so the store lands in exactly the state a real model produces (every
`*FromDoc` reader runs). Only the fetch is skipped.

## The legacy-flag bridge

`CellBuilderPanel` returns null unless `cellBuilderStore.panelVisible`;
`SimulationDataInfoPanel` unless `tableNavStore.isPanelOpen`. In the classic UI those
flags **were** the visibility model — in the shell the dock is. Without a bridge a docked
panel mounts and then decides not to draw, which is an invisible failure.

`useLegacyFlagSync` mirrors dock visibility into those flags, one-way (layout → flag).
Two-way sync between a boolean and a layout tree invites a feedback loop; the reverse
direction stays where it belongs, in the actions that own each panel. The flags are not
deleted because external callers still set them — the Properties panel's "Show in data"
opens the table by flipping `isPanelOpen` — and deleting them means editing components
under the fence. They go at M8 with the classic UI that needs them.

## Also in M5

- **Node editor as a dock panel.** Its ReactFlow body was extracted verbatim into
  `NodeEditorBody`, shared by the classic floating window and the new dock panel, so the
  two cannot drift. The dock supplies the frame, so the react-rnd wrapper and its
  hand-styled header are gone from the shell path; the two header actions (reload
  procedures, pop out) survive as toolbar buttons calling the same handlers. It sits in
  the **bottom** dock — a node graph is wide, and the classic 800×600 floating window was
  covering the model the procedures act on.
- **Build rail: undo / redo**, delegating to the store so the undo stack keeps exactly one
  owner. There is deliberately no "Add cell" button: cell placement is a viewport gesture
  driven by `CellBuilderController`, and a rail button would imply a mode the tool does
  not have.
- **The compile gate is in the status bar.** Previously "you have uncommitted changes" was
  visible only *inside* the cellbuilder panel — so with the panel closed you could edit
  for a while with no indication a compile was owed. It now shows the model name, a
  pass/warn dot and a Compile button running the same action as ⇧↵.
- **Per-mode dock sizes.** Build opens its right dock at 400 px; at the 300 px default the
  Builder's content truncates, which reads as a broken panel rather than a narrow one.

## Numbers

| | M4 | M5 |
|---|---|---|
| Tests | 315 | **318** (0 skipped) |
| `dist/index.html` | 2,899,394 B | 2,902,435 B (**+2.69 %** vs M0, budget +8 %) |

## Deferred, with reasoning

**`CellBuilderPanel` (1963 lines) is not split or re-chromed.** The plan had both here.
The panel now works correctly in the dock, and the split's only purpose is to make a
re-chrome tractable — so doing the split *without* the re-chrome is churn and risk on the
most complex authoring surface in the app for zero user-visible gain, while doing both
properly needs more care than the tail of this milestone allows. It wants a dedicated
pass, and it is the single largest remaining item in the rewrite.

Its buttons stay on the `noAdHocChrome` allowlist until then, which is the honest state:
the burn-down number reflects work not yet done rather than work hidden.

---

# M6 — Data mode

Review it at **`npm run dev:rest`** → `http://localhost:5173/?shell=1` → **Data**.

## Storage and Convert, side by side

`/convert` was a separate page: no way back to the viewer, and no sight of the storage it
reads from. It is now a Data-mode panel in the right dock, beside the file list in the
left. You pick a file on one side and convert it on the other.

## The scope picker moved into the title bar

Scope is the most consequential context in a multi-project deployment — every file,
conversion and job belongs to one. It lived inside the Options drawer: three clicks from
the file list it governs, and invisible while you were using it.

The switch itself goes through a new shared `applyScopeChange`, extracted from the
classic drawer, because the *teardown* is the part that matters and is easy to get wrong
by reimplementing: clear the file list, unload the scene, refresh. Without it you see the
previous project's files still listed and a stale 3D scene from a project you are no
longer in — which reads as a data-leak bug rather than a missing refresh.

## A toast host

`ConversionProgress` positioned itself fixed bottom-right; the upload toast fixed
bottom-left; each with its own z-index guess. That is how two toasts overlap and how one
ends up behind a panel. One host, one corner, one layer from the registry — `Z.toast`,
above `Z.contextMenu` (a job failing while a menu is open must still be readable) and
below `Z.dialog` (a modal you are actively answering wins). The components keep their own
visibility logic, so *when* a toast appears is unchanged.

## The dev fixture now speaks flatbuffers

StorageBrowser's file list does **not** come from a JSON endpoint — it goes through the
flatbuffer RPC at `POST /rpc`, the same envelope the websocket transport uses. Serving
JSON would have left the panel permanently empty, so the dev server builds a real
`Message`. The envelope shape is not obvious and cost a round of debugging: the client
dispatches on `commandType === SERVER_REPLY` and *then* on `serverReply().replyTo()`, so
a message typed `LIST_FILE_OBJECTS` directly is silently ignored — no error, just an
empty list.

Unmatched `/api` routes now return a JSON 404 instead of falling through to the SPA
shell, which was producing `Unexpected token '<'` errors that said nothing about the
actual problem.

## A third silent-loss bug

**The shell never mounted `AuthGate`**, so it never called `/api/me`. Besides being the
sign-in gate, that is what populates the scope store and the admin flag — so the shell
booted with no scopes, the picker rendered nothing, and every scoped request fell back to
a default. Nothing errored; it was simply wrong. Now mirrored from the classic path and
covered by a test.

That is three of this kind now (plugin regions in M4, legacy visibility flags in M5, REST
bootstrap here). They share a shape worth naming: **the classic UI did bootstrap work
inside components the shell does not render.** Anything remaining in `Menu.tsx`,
`AppBody` or `RestModeUI` should be assumed guilty until checked.

## Numbers

| | M5 | M6 |
|---|---|---|
| Tests | 318 | **320** (0 skipped) |
| `dist/index.html` | 2,902,435 B | 2,904,479 B (**+2.77 %** vs M0, budget +8 %) |

## Deferred

**`StorageBrowser` (2526 lines) is not split or re-chromed**, for the same reason as
`CellBuilderPanel`: it works correctly in the dock, and the split only pays off alongside
a re-chrome that needs its own pass. Its highest-risk parts — the row context menu and
the presigned direct upload — want manual verification against a real backend, not a
fixture.

**`/admin` is still its own route.** Folding 14 tabs into a Data workspace is M7's job,
where they get the codemod pass; doing it here would mean moving them twice.

---

# M7 — discoverability

"Features exist but users don't know they're there" was one of the four cited pains. The
old model was ten identical icon buttons and a drawer: if you did not already know a
feature existed, nothing told you.

## One shortcuts registry

`src/shell/shortcuts.ts` is now the single description of every binding. There were
three: the actual handler, a hand-written cheat sheet in `ShortcutsModal`, and whatever
each tooltip happened to say. Two of those were documentation, and documentation drifts
**silently** — a tooltip promising a renamed key is wrong forever and nothing notices.

`shortcuts.test.ts` parses `setupCameraControlsHandlers` and checks the registry against
it **in both directions**: a promise the handler does not keep fails, and so does a
binding nobody documented. `docs/SHORTCUTS.md` is generated from the registry
(`npm run gen:shortcuts`), so the reference cannot be the stale copy.

## Command palette

`Ctrl+K` (or `Ctrl+Shift+P`, or the search button in the title bar). Commands are
**generated** from the panel, mode and shortcut registries — a hand-written list would be
a fourth copy of facts that already exist, and the copy that goes stale because nothing
breaks when it does. Add a panel and it appears; add a shortcut and its keys show up
beside the command.

It also teaches the shortcuts rather than hiding them in a modal: every command that has
one displays it.

Two decisions worth recording:

- **Panel commands are scoped to the current mode.** Offering "open the Builder" from
  Results would either do nothing or silently switch mode, and silent mode switches are
  what the non-modality contract forbids. Switching mode is itself a command, so the path
  exists — it is just explicit.
- **The palette closes before running a command.** A command that changes layout or mode
  re-renders the tree beneath it, and running it while the overlay is still up leaves the
  change behind a scrim the user then has to dismiss.

Ranking lives in `commandFilter.ts`, separate from the wiring, for the same reason
`coreProviderRules` is separate: the action imports reach a vite `?worker&inline` module
that only a bundler resolves. Ranking is also the half users feel — typing three letters
and getting the wrong first result is what makes a palette not get used twice — so it has
its own tests: titles outrank keywords, prefixes outrank mid-word hits, and "fta" finds
"Fit all to view".

## Admin folds into Data mode

`AdminPanel` is now a Data-mode bottom-dock panel, gated on `isAdmin && isRestMode`. It
gates internally too, but a panel that renders "you are not an admin" is worse than one
that is simply not offered. The `/admin` route still works as a deep link.

## Numbers

| | M6 | M7 |
|---|---|---|
| Tests | 320 | **335** (0 skipped) |
| `dist/index.html` | 2,904,479 B | 2,913,210 B (**+3.07 %** vs M0, budget +8 %) |
| Files hardcoding palette colours (`ui:audit`, all of `src/`) | 81 | **82** |
| Enforced allowlist (`src/components/**`) | 80 | **80** |

The audit metric went the **wrong way by one**: the new `CommandPalette` uses a
`bg-black/40` scrim for its modal backdrop. Recorded rather than quietly excluded.

Writing that up exposed a real gap in the enforcement itself: **the test only scanned
`src/components`, so all of `src/shell` — the newest code in the codebase, written after
the design system existed — was exempt from the standard it was meant to demonstrate.**
`src/shell` is now checked too, with **no allowlist**: there is no legacy there to
grandfather. It passes today, so this locks in rather than reveals.

## Verified, and one thing not

The palette opens from the title-bar button, generates 24 commands in Inspect, filters
correctly ("leg" → the legend command), and shows shortcut hints. Admin registers and
gates correctly.

**The keyboard shortcut could not be verified through the automation harness**: Chrome
claims both `Ctrl+K` and `Ctrl+Shift+P` before the page sees them, so synthetic keys never
arrive. The handler is verified by direct event dispatch, and the button is verified
end-to-end — but a real keypress in a real browser is worth one manual check.

That limitation is also why the title-bar button exists at all: a palette reachable only
by a shortcut you must already know does not solve discoverability, which was the point.

## Deferred

**Empty states**, **saved workspaces** and the **customisable tool rail** are not done.
`PropertiesPanel` already has a proper empty state from M3 and empty docks render nothing
rather than chrome, so the remaining cases sit inside `CellBuilderPanel` and
`StorageBrowser` — the two panels awaiting their re-chrome pass. Doing them now would mean
touching those files twice.

`ShortcutsModal` is not yet regenerated from the registry; it is classic-UI chrome that
goes at M8, and the palette already supersedes it in the shell.

### The embed defect from M0 — fixed, and it was not what it looked like

M0 recorded that host-page CSS cascades *into* the embed and guessed that converting
controls to token-setting primitives would fix it. **That guess was wrong**, and the
real cause is worth writing down.

The embed's stylesheet is Tailwind v4 output, which lives entirely inside
`@layer properties/theme/base/components/utilities`. **A style in a cascade layer
loses to an unlayered style unconditionally — specificity is not even considered.** So
a host page's plain `button { background:#c00 }` beat our `.bg-blue-700` class.
Primitives would have lost too: they are utilities in `@layer utilities`.

The fix is `flattenLayers()` in `vite.plugin-embed-css.mjs` — strip the `@layer`
wrappers from the embed build so its rules compete on ordinary specificity, where a
class beats an element selector. Layer order and source order agree in Tailwind's
output, so relative precedence is preserved. Only the embed is flattened; the
standalone app owns its whole document and keeps normal layering.

Verified in `embed/dev.html`: the embed's toolbar keeps its own blue/4 px styling,
the host's heading and pill button are untouched, and `--ada-radius-md` resolves
through the hoisted block.

`@layer properties` still survives flattening. It is Tailwind's `@property` fallback
block — it declares `--tw-*` initial values via `@supports` and contains no competing
utilities, so it is harmless. Left as-is rather than special-cased.

**A parser bug this exposed, worth remembering:** the block scanner treated `\'` as a
string delimiter. Tailwind compiles a `content-['']` utility (used by `Checkbox`) to
the selector `.checked\:after\:content-\[\'\'\]`, so the scanner entered string mode
and stayed there for the rest of the file, swallowing every brace. Both the hoist and
the flatten silently no-opped — no error, just an unstyled embed. CSS escapes apply in
selectors, not only inside strings. Covered by two regression tests.

---

## Preferences re-chrome

Every control in Preferences now renders on the design system. This closes the
"inconsistent look" pain for the one panel the user reaches most often to change
something, and it removes 6 files from the `noAdHocChrome` allowlist (80 → 74).

**The split.** `OptionsComponent` drew its own bordered, separately-scrolling panel.
In the classic UI that is correct — it sits in the info-box column and has to look
like its neighbours. In the shell it produced a box inside the dock's box, with a
second scrollbar. The content moved to `options/OptionsBody.tsx`, which has no chrome
of its own; the shell's registry now points there, and `OptionsComponent` is reduced
to the classic mobile-drawer / desktop-panel wrapper that renders it. Same split as
the M3 info boxes, same reason, and both halves go at cutover.

`ShortcutsModal` stays on the classic wrapper rather than moving into the body: the
shell supersedes it with the command palette, so putting it in the shared body would
have shipped two answers to the same question.

**Converted:** `DisplayOptions` (11 rows), `PointSizeOptions`, `PerformanceOptions`
(13 toggles + a select + a slider), `ThemeOptions`, `ExperimentalOptions`,
`RestSection`, `ActionButtons`, `ShortcutsModal`, `OptionsComponent`.

**Choices worth recording:**

*`Switch`, not `Checkbox`, for all of them.* Every one of these settings takes effect
the moment you flip it — nothing here is a pending value confirmed by an OK button.
A checkbox implies "selected, will be applied"; a switch implies "on, now".

*The `<hr>` rules in Performance became named `Section`s* — Materials, Rasterisation,
Loading, Picking, Frontend metrics. A divider tells you something changed but not
what, and with thirteen switches in one column that is the difference between a list
you can navigate and one you have to read end to end. The `reload` markers became
`Badge tone="warn"` instead of parenthetical prose.

*The theme preset cards keep their inline `style={{background: p.theme.bg}}`.* That
is not ad-hoc styling that escaped the sweep — it is the preview. The card is
supposed to look like the thing it selects, and the value comes from preset data.

*`RestSection`'s purple "Admin panel" and blue "Convert files" are now plain
secondary buttons.* Two maximally-loud full-width buttons for things you press
occasionally, in a colour (purple) that meant nothing anywhere else in the product.
Admin is marked by a badge now rather than by being shouted.

Behaviour was preserved deliberately in two places that look like bugs and are not:
`PerformanceOptions` keeps its inverted antialias handler (`checked={!antialias}` —
the row is labelled *Disable* antialias), and `OptionsBody` passes
`defaultOpen={false}` to every section because the shared `CollapsibleSection`
defaults to open and the drawer's sections have always started closed.

### Two defects this surfaced

**`Slider` collapsed to zero width inside a flex row.** The primitive wrapped its
track in a `flex items-center gap-2` div with no width of its own. Dropped into the
point-size row next to a number field, that wrapper resolved to `flex-basis: auto`
over zero-width content, collapsed, and the track vanished under its neighbour. The
`flex-1` was on the `<input>`, which only made it fill its already-collapsed parent.
Fixed on the primitive (`w-full min-w-0` on the wrapper), not on the three call
sites.

**`className="w-20"` on an `Input` did nothing.** `Input` sets `w-full` itself, and
which of the two wins is decided by stylesheet order, not by prop order — so the
"override" silently lost and the field took the whole row. Width now goes on a
wrapper element, which is deterministic. Worth watching for wherever a caller tries
to override a primitive's own layout classes.

Neither was visible in the classic UI, where the drawer is wide enough that the
collapse did not overlap anything. Both only showed up in the shell's narrower float
panel — an argument for reviewing panels at their real width, not at their most
generous one.

---

## Two recovery fixes

Both came out of using the shell rather than reading it, and both are cases where the
shell made something *more* reachable than the classic UI did without adding the
guardrail that reachability needs.

### Switching scope no longer discards your model silently

`applyScopeChange` unloads the scene, because the model belongs to the scope you are
leaving. In the classic UI that control was three clicks deep in the Options drawer,
so it was hard to hit by accident. The shell moved it to the title bar — correct for
visibility, since scope is the most consequential context in a multi-project
deployment, and wrong for a destructive default. Loading a large model is minutes of
work and there is no undo.

`requestScopeChange` now asks first, and only when there is something to lose — a
confirmation on an empty scene is a dialog that teaches people to dismiss dialogs.
Both call sites (title bar, classic drawer) go through it, so they cannot drift.

Declining has to put the `<select>` back: the element has already moved to the new
option by the time the promise resolves, and a picker claiming a scope you are not in
is worse than the original bug.

**The first version of this guard did nothing.** It asked `modelState.loadedSourceNames`,
which only the storage browser populates — so `?demo=1`, a `.show()` push over the
websocket, and drag-and-drop all presented an empty set with a full scene. It now also
consults `modelKeyMapRef`, which is what `clear_loaded_model` actually tears down and
therefore the honest answer. Caught by clicking the control, not by reading the code;
the test that covers it is named for the case.

New shared machinery, both on the plan as Tier 2:

* `components/ui/Dialog.tsx` — the modal shell. Five hand-rolled versions existed
  (FilePicker, FolderPicker, Shortcuts, WorkerInfo, FieldPicker), each with its own
  backdrop, own z literal, and its own answer on whether Escape works. Deliberately
  not a `<dialog>` element: `showModal()` promotes to the browser's top layer, which
  escapes the embed's `@scope` wrapper and would land unstyled over the host page.
* `ui/confirm.ts` — an awaitable confirmation. `window.confirm` was the alternative;
  in the embed it prefixes the host page's origin, so a docs page would say
  "docs.example.com says: Discard the loaded model?", which reads as phishing rather
  than as part of the viewer. A second request cancels the first rather than stacking:
  the one the user never saw resolves as declined, which is the safe direction.

### Layout reset is in permanent chrome, not just the command palette

Layout persists per mode, so one bad afternoon of dragging follows you across reloads
and the app simply looks broken. Reset existed — in the command palette, which is
precisely what you cannot be expected to find while the UI is the thing that is wrong.
The Panels menu now ends with "Reset <Mode> layout" and "Reset every mode's layout".

**This immediately exposed a z-index bug that the registry could not have caught.**
The Panels dropdown carries `z-index: contextMenu` (50) and floating panels carry
`float` (40), so the menu should win. It did not: the title bar is a **grid item** with
`z-index: dock` (20), and a z-index on a grid item applies even at `position: static`
— and brings a stacking context with it. The menu's 50 was being resolved *inside* that
context, so every float panel drew over it. The registry's ordering was right; the
containment was the bug.

Fixed by portalling the menu to `<body>`, where its z means what it says. That also
meant teaching dismiss-on-outside-click about the portal, since the menu is no longer
a descendant of the trigger and clicking a panel toggle would otherwise close the menu
— when toggling several panels in a row is the normal way to use it.

Worth generalising: **any popover rendered inside a docked region has this problem.**
`zIndex.test.ts` checks the registry is ordered, which is necessary and not sufficient;
a z-index is only comparable against things in the same stacking context. Popovers
belong in a portal, full stop.

### Not changed, deliberately

Menus inherit the panel surface, which in the default "Slate glass" preset is 62%
opaque, so viewport content ghosts faintly through them. Menus arguably want to be
opaque regardless of the panel theme — you read them for a fraction of a second over
arbitrary content. That is a product-wide look decision affecting every popover in
every preset, not something to fold into a bug fix, so it is left as-is and noted here.

---

## CellBuilderPanel split (phase 1 of 2 — no behaviour change)

`CellBuilderPanel.tsx` was 1963 lines holding a panel shell, six tab bodies and five
helper components in one scope. Split into `viewer/cellbuilder/*`; the shell is now 421
lines and does what a shell should — header, tab strip, mobile sheet drag, pinned
footer.

| File | Lines | Was |
|---|---|---|
| `CellBuilderPanel.tsx` | 421 | the whole thing |
| `BuildTab.tsx` | 692 | inline in the render |
| `SystemsTab.tsx` | 222 | top-level in the same file |
| `ConnectionAdder.tsx` | 198 | top-level, + its port/orient tables |
| `ToolsTab.tsx` | 196 | inline in the render |
| `ViewTab.tsx` | 166 | inline in the render |
| `Section` / `IconOverlaySection` / `CompileLogSection` / `describeToolState` / `chrome` | 196 | top-level |

**Deliberately a pure move.** Bodies were sliced out verbatim and only import headers
were written. The re-chrome onto the design system is a separate commit, because the
whole point of two-phase is that if something breaks, `git diff` tells you whether it
was the move or the restyle. Two references needed re-deriving in their new home
(`compileBusy` in ViewTab, the two dropdown-menu states into BuildTab) and nothing else
— the tabs turned out to be far less entangled than the file's size suggested.

**The contract that had to survive: tabs are hidden, not unmounted.** Every tab body
renders inside `className={tab === x ? … : "hidden"}`, so switching tabs keeps each
tab's local state — which sections you expanded, which menus were open. Extracting to
components would have broken that the moment anyone wrote `{tab === "build" && <BuildTab/>}`.
The `hidden` wrapper stays in the shell and the components sit inside it. Verified in
the browser: expand a Build section, go to Tools, come back — still expanded.

`equipMenuOpen` / `openingMenuOpen` and their button refs moved *into* BuildTab, since
nothing outside that tab ever read them. `compileMenuOpen` stayed in the shell, since
it belongs to the footer's compile split-button.

**The allowlist grew by six, and that is correct.** `noAdHocChrome` immediately failed
on the new files — the ad-hoc chrome did not go away, it changed address. Registering
them keeps the guard honest rather than silently widening it; phase 2 deletes all six
lines at once. The class strings themselves are collected in `cellbuilder/chrome.ts`
unchanged, so the re-chrome is one edit rather than five.

---

## CellBuilderPanel re-chrome (phase 2 of 2)

113 palette classes across the cellbuilder became semantic tokens, and the three shared
class strings became the design system's own.

**`chrome.ts` no longer describes a button — it asks for one.** `btn`, `btnGray` and
`inputCls` were hand-written Tailwind (three of the ~12 recipes M0 found for the same
three roles). They are now `buttonClasses("primary","sm")`, `buttonClasses("secondary","sm")`
and `fieldClasses("sm")` — the same functions `<Button>` and `<Input>` call. There is one
definition of what a secondary button is, and the cellbuilder cannot drift from it.

That required exporting `buttonClasses` / `fieldClasses` from the primitives. This is a
deliberate escape hatch with a narrow justification: these roles land on several hundred
dense tool rows whose elements carry their own refs, aria wiring, menu anchors and split
borders (`btn + " rounded-r-none"`). Swapping each for `<Button>` is a rewrite, not a
re-chrome, and phase 1 exists precisely so the two are not mixed in one diff. Both
functions carry a docstring saying to prefer the component.

**The grey collapse.** Five greys were doing the work of three roles — `text-gray-100/200/300`
all meant body text, `-400` meant de-emphasised label, `-500/600` meant hint. They map to
`text-content`, `text-content-muted`, `text-content-subtle`. This is where most of the
"messy" impression came from: the same role rendered three different shades depending on
which day the line was written.

**One disclosure instead of two.** `common/CollapsibleSection` (Scene panel, Preferences)
and the cellbuilder's private `Section` were the same component with different chrome and
a count badge. Both deleted; `ui/CollapsibleSection` has a `divider` variant (a rule above
the header — a column of primary groups) and a `boxed` variant (an outlined tinted card —
occasional groups sitting among ordinary rows, where the box says "container, not another
row"). `Section` in `Panel.tsx` remains the non-collapsible sibling.

`defaultOpen` deliberately has **no** default on the shared component. The two originals
disagreed — divider sections opened, boxed ones did not — and inheriting either silently
would have flipped five cellbuilder groups open on a panel whose whole point is to start
compact. The cellbuilder keeps a three-line `boxedSection` wrapper pinning its variant and
default, rather than repeating `variant="boxed" defaultOpen={false}` at five call sites.

**Allowlist 80 → 73.** All seven cellbuilder entries are gone, including the six that
phase 1 had to add. `CellBuilderPanel.tsx` — on the allowlist since it was written — is
off it.

### A trap worth naming

The bulk palette→token rewrite edited the *comment* in `chrome.ts` that quoted the old
class names as examples, turning "these used to be `bg-blue-600`" into "these used to be
`bg-accent`". Harmless here and caught by reading the diff, but a codemod that rewrites
class strings will happily rewrite prose about class strings. Track B's codemods should
operate on JSX attribute values, not on file text.

---

## The menu bar

The per-mode tool rail was the wrong idea, taken from the right place. Cinema 4D's
dynamic palettes work because its modes are five closely-related modelling contexts
sharing most of their tools. Here the four modes are genuinely different applications,
so the rail turned over almost completely between them: nothing had a fixed address, and
you cannot build a memory of where a command lives if it moves when the mode changes.

**File · Edit · View · Tools · Window · Help.** Same menus, same order, in every mode.

### The three discovery mechanisms, and what each is for

* **The menu bar** is the complete index. Every command, fixed place, same order. It is
  the only one that answers "what can this application do".
* **The command palette** answers "run the thing I can already name". It is useless for
  discovery — you cannot search for a word you have never seen — which is why it was
  never sufficient on its own.
* **The tool rail** is now for the handful of actions you reach for constantly without
  looking.

All three read from **one command registry**. A hand-written menu would be a fifth copy
of facts that already live in the panel registry, the mode list and the shortcut
registry, and it would be the copy that goes stale, because nothing breaks when it does.
`menuModel.ts` names command *ids*; a typo is caught by a test, not by a user finding a
gap where an item should be.

### Disabled, not hidden

Commands that cannot act right now are greyed with the reason as their tooltip —
"Nothing is selected", "No result set is loaded", "No procedural model is open". This is
the whole point of having a menu bar. A menu whose contents depend on state is a menu you
cannot learn, and "why is that greyed out" is a far better question than "where did it
go". `Command` gained `enabled` / `disabledReason`; enablement is evaluated when the menu
opens, so it is a snapshot of live state rather than a stale one.

Cross-mode panels are listed with their mode's name on the right — "Show Simulation
··· Results". Choosing one switches mode, which is a mode switch the user asked for by
name. The non-modality contract forbids *automatic* switches, not user-initiated ones.
The palette still scopes panels to the current mode, because there the user is typing a
name rather than reading a list, and a silent jump would be a surprise.

### What this made visible

Five commands existed only as key bindings — Shift+C and the Shift+arrow tree
navigation, bound in `setupCameraControlsHandlers` and documented nowhere a user would
look. `selectionActions.ts` gives them a name and a second entry point, delegating to the
same `copySelectionNames` / `treeNavigation` functions the key handler calls. This is the
menu's real value showing up before it even shipped: laying out a complete index forces
you to notice what has no home.

`Help ▸ Keyboard shortcuts` renders `shortcuts.ts` directly, grouped by *when a key is
live* rather than by topic — a builder-only key listed beside a global one is how people
conclude a shortcut is broken. The classic `ShortcutsModal` keeps a hand-maintained
second copy of the same list; it dies at cutover.

### Popovers must be opaque

Panel themes are rgba — the default "slate glass" is 62% opaque, which is right for a
panel you park beside the model and wrong for a menu you read in a fraction of a second
over arbitrary 3D content.

CSS cannot flatten that alpha: `color-mix` over an opaque colour still yields a
translucent result, because mixing is not compositing. The fix is to stack — an opaque
`bg-surface-0` layer blocks the background, and a tinted `bg-surface-1` layer inside
supplies the theme's colour. The composite is opaque and still themed. **This is the
pattern for every popover**, and the earlier note deferring it as a taste question was
wrong: for a menu it is legibility, not taste.

### Deferred

`Edit` currently holds only Undo/Redo/Copy/Select, because those are the only edit
commands that exist as commands. That thinness is honest and worth leaving visible — it
is the menu doing its job of showing where the gaps are.

---

## Chrome restructure: two rows, a stable rail, and a mode order that follows the work

### Menus above modes, not beside them

The menu bar and the mode switcher are not peers. The menus are the application; the
modes are a setting within it. Side by side they read as one row of equals, and "File"
sitting next to "Inspect" invites you to read File as a fifth mode. Two rows: application
chrome on top (menus, scope, palette), where-you-are below (modes, and the mode's tools).

### The rail stopped changing; the mode's tools moved under the mode buttons

Mode-specific tools now live in a horizontal strip directly beneath the mode switcher.
The changing contents sit under the control that changes them, which is what makes them
legible — the strip is visibly part of the mode rather than part of the app.

The rail keeps only what means the same thing everywhere: camera, visibility, section,
measure, undo/redo.

**Undo and redo were the clearest symptom.** They lived in the Build rail, which asserted
that undo is a modelling feature. Undo is universal in every application anyone has ever
used. They are in the rail now, greyed with "Nothing to undo here yet" when there is no
document with a history. That is the general rule: *a universally understood feature stays
put and greys out; it does not vanish and reappear.* Greyed also means the tooltip has to
say why — a control that is dim for no stated reason is one people stop trying.

**Panel toggles left the rail.** The menu bar lists every panel with its shortcut, which
is a better index than a column of unlabelled icons, and the duplication cost the rail the
room its actual tools need.

### Mode order and the Files rename

`Library · Build · Inspect · Results` — work flowing left to right: get a model in,
author it, examine it, post-process results from it. That is how people describe their
own work, so it is learnable in a way "most-used first" is not. Build precedes Inspect
because you cannot inspect what does not exist yet, and it puts the two "look at what is
there" modes side by side.

"Data" named the code's concern rather than the user's. The first replacement, **Files**,
was worse in a way that is easy to miss on paper and obvious on screen: it sat one row
below the **File** menu — two labels a keystroke apart, in the same chrome, meaning
different things. The label is **Library**: a place you draw models from, which is what
the storage browser makes it, and impossible to confuse with a file operation.

The mode **id stays `data`**: layouts persist per mode under `ada:layout:v2`, and
renaming the key would silently reset everyone's arrangement.

### What Inspect is for — the honest answer

Asked directly, the registry answers it: **Inspect owns nothing exclusively.** Outliner,
Properties and Preferences are `modes: "all"`; Scene is in Inspect, Build *and* Results;
its two rail tools were section planes (which opens the Scene panel, available in three
modes) and measure (not wired up). Zero exclusive panels, zero exclusive tools.

That is not an argument for deleting it. It means Inspect is not a specialisation at all —
it is the **base state**, and what it offers is the *absence* of the other modes'
apparatus: the model, the tree, properties, and nothing else on screen. That is worth
having, and it is worth saying out loud in the mode's own hint, which now does.

It also explains why its tool strip is empty, and why that is left empty rather than
padded to make the mode look busy. An empty strip is the honest rendering of "nothing
extra here", which is the entire point of the mode.

### A bug the reorder exposed

`modeDef` fell back to `MODES[0]` for an unknown id — coupling the fallback to *display*
order. The moment that order changed, an unknown persisted mode would have dropped the
user into Files, which needs REST and is an empty workspace on desktop: the worst place to
strand someone whose layout blob just failed to load. There is now an explicit
`DEFAULT_MODE`, and the store's initial value reads from it too.

### Still to do

The right dock still shows one panel at a time and hides the Scene panel's six sub-tabs
behind tabs even when the dock is tall enough to stack them. Addressed next.

---

## Stacked docks, and the Files→Library rename

### The right dock stops hiding things when it has room

A dock now picks between two arrangements from its own measured height:

* **Tabbed** when short — the strip plus the active panel. Correct when space is scarce;
  the old UI let every panel be open at once in one column, which is how "too much on
  screen" happened.
* **Stacked** when tall — every panel visible at once under its own header. Tabs are a
  response to scarcity, and applying them when there is room just makes the user click to
  see what would have fitted anyway.

It follows the window and the splitter with nothing to configure. Panels are mounted in
both arrangements — the tabbed one hides the inactive ones rather than unmounting them —
so switching costs nothing and loses no panel state, scroll position or in-flight edit.
The bottom dock is always tabbed: it is wide-and-short by design (the FEA table, the
conversion log) and stacking leaves each panel too short to read.

The thresholds and the hysteresis live in `dockArrangement.ts` as a pure function, and
they are the part worth testing — a browser cannot easily be driven to the exact heights
where the behaviour changes. **Entering and leaving use different heights on purpose**:
with one threshold, dragging a splitter across it flips the arrangement every frame, and a
layout that flickers reads as a fault rather than a feature. The test asserts the band
holds its state from both directions.

Per-panel controls (pin, float, close) move into each panel's own header when stacked,
because "close the active panel" has no meaning when they are all active.

### Files → Library

Two names collided. Naming the mode **Files** put it one row below the **File** menu —
two labels a keystroke apart, in the same chrome, meaning different things. That is easy
to miss while writing the structure and impossible to miss once it is on screen.

**Library** is a place you draw models from, which is what the storage browser makes it,
and it cannot be read as a file operation. Admin and jobs are what the name undersells;
both are rare and one is admin-only — the same trade "Files" made, without the collision.

The mode **id remains `data`** through both renames. Layouts persist per mode under
`ada:layout:v2`; renaming the key would silently reset every user's arrangement, and no
label is worth that.

### Mode order, settled

`Library · Build · Inspect · Results`. Build precedes Inspect because you cannot inspect
what does not exist yet, and it puts the two "look at what is there" modes next to each
other.

---

## The mode toolbar, filled in; modes moved up and coloured

### Modes join row 1

The mode buttons sit beside the menus now, separated by a rule, one type-step smaller.
They are a persistent statement of *where you are*, which belongs with the other
persistent chrome — and giving them a row of their own made them the loudest thing on
screen, which they do not need to be. Row 2 is now purely the mode's tools, which makes
that row's changing contents read correctly: what changes with the mode is the strip, not
the frame around it.

**The active mode is green** (`bg-pass-subtle text-pass`, from the theme's semantic
palette rather than a literal, so it moves with the preset and stays legible on the light
one). White-on-raised said "this button is pressed", which every toggle in the app also
says. Colour says "you are HERE" — a different claim, and the one worth making: this is
the only place in the chrome that answers which of four applications you are looking at.

### The strip actually has tools now

It was thin because the first pass only wired actions that already had handlers. Each
mode now carries what it is actually for:

| Mode | Tools |
|---|---|
| Library | Upload · Convert · Refresh |
| Build | **Move · Rotate · Resize** (gizmo toggles) │ Compile preview · Groups · Section |
| Inspect | Quantities & take-off · Scene tools · Mesh quality · Section |
| Results | **Play/pause** │ Legend · Data table · FEM concepts · Section |

Several are doors onto Scene-panel tabs that already existed and were reachable only by
opening the panel and finding the right tab. Nothing new was implemented; existing state
got a visible entry point.

Two considered exceptions to "one control per piece of state":

* **Gizmo toggles** are the same `gizmoMode` G/R/S set, so the toolbar shows sunken when
  you press the key, and pressing the active one clears it exactly as Escape does.
* **Play/pause** also exists on the Simulation panel's transport. The transport lives on a
  panel you may have closed, and a result set you cannot start without reopening a panel
  is the kind of thing people file as a bug. Both drive `isPlaying`; neither holds a copy.

### A control that lied, caught by clicking it

The first version offered all three gizmos for any selection. The controller does not:
rotate is **equipment-only**, resize is **cell-only**, and no gizmo touches a loft — a cell
has no meaningful rotation, equipment has no resize handles.

So the toolbar would have set a `gizmoMode` the controller then refuses to act on. Worse:
pressing `R` on a cell correctly does nothing, so the keyboard and the toolbar would have
disagreed about the same operation. Found by pressing `R` after clicking the toolbar and
noticing the store had not moved — not by reading the code.

The rule now lives in `gizmoRules.ts` as a pure function with tests, because it is stated
in two files and duplicated rules drift. The test names the reason for each restriction so
the next person does not "fix" the asymmetry.

---

## The Scene panel: split, and its tabs stack too

### Another box in a box

`SceneInfoBox` drew its own bordered, separately-scrolling frame — the same fault
`OptionsComponent` had, and invisible until you put it in a dock. Content moved to
`SceneBody`; `SceneInfoBox` is now the classic float / bottom-sheet wrapper, and
`ScenePanel` is the shell's three-line entry point.

The contextual-tab logic (does this model have FE concepts? joints?) stayed in a shared
`useSceneContextTabs` hook rather than moving into the body. It is a question about the
loaded model, not about presentation, and both entry points must get the same answer or
the classic panel and the docked one will disagree about which tabs exist.

### Six tabs become a column when there is height

Same idea as the dock, different arithmetic, and the difference matters:

* A stacked **dock panel** is always open, so it needs a full body's height each.
* A stacked **tab** becomes a *collapsible*, so it costs a header row until opened. The
  budget is `headers × count + one body`. Charging each tab a full body would mean six
  groups never stack on any real screen — which is why `tabArrangement.ts` is a separate
  rule from `dockArrangement.ts` rather than a reused one.

Why bother: a strip of six labels admits one group at a time, and in a narrow panel the
strip itself scrolls, so some labels are not even visible. A column shows every heading at
once and lets you open two together — which is the point when you are comparing take-off
against groups. The group the store points at opens, so deep links and the toolbar's
"Quantities & take-off" still land you in the right place in both arrangements.

### Two real bugs found by measuring instead of assuming

**1. Panels were not filling the dock.** `DockHost`'s tabbed wrapper was `h-full`, a plain
block. A panel that says `flex-1` to fill its host resolves that against *content* inside
a block parent, so `SceneBody` sized to its content: **277px inside a 978px dock**. Every
panel that wants to fill was quietly getting a fraction of the height it had, and any
panel making its own layout decisions from a measurement was measuring the wrong number.
The wrapper is `flex h-full flex-col` now.

**2. The arrangement was decided solely by the ResizeObserver's first callback.** That
callback is delivered on the frame pipeline, which the browser **suspends for a hidden
tab** and throttles under load — so a panel opened in a background tab would sit in the
default arrangement until something resized it. Both `SceneBody` and `DockHost` now
measure synchronously in a `useLayoutEffect` first and keep the observer for changes.

The second one is worth dwelling on because of how it surfaced. The feature simply did not
work in the browser and no amount of re-reading the code explained it; a *fresh*
`ResizeObserver` created from the console did not fire either, and `document.hidden` was
`true`. The harness had exposed a genuine defect that a foreground manual test would have
sailed past. `data-arrangement` on the body is kept as a permanent probe.

---

## StorageBrowser split (phase 1 of 2 — no behaviour change)

2526 lines → 1724, with the rest in named files beside it.

| File | Lines | Was |
|---|---|---|
| `StorageBrowser.tsx` | 1724 | the whole thing |
| `FileRow.tsx` | 376 | top-level in the same file |
| `VersionsTree.tsx` | 264 | top-level |
| `FolderRow.tsx` | 221 | top-level |
| `classifyFiles.ts` | 109 | top-level |
| `storageHelpers.ts` | 72 | top-level |
| `Spinner.tsx` | 11 | top-level |

**`classifyFiles` is the reason this split was worth doing beyond line count.** It is the
only real logic in the storage browser — everything else is presentation over a server
response — and it was untestable while it sat in a file that reaches the model worker.
Thirteen tests now cover it, and one of them protects a rule that would otherwise rot
silently:

> The commit sort key prefers the sidecar's **git timestamp** over S3 **mtime**, because
> re-running CI on an older commit refreshes that commit's mtime. Sorted by mtime, the
> "latest" build is whichever was rebuilt most recently — which is still a plausible-looking
> commit, so nobody notices it is the wrong one.

**A trap in the mechanical part.** Each extracted file inherited StorageBrowser's whole
53-line import block, of which most was dead — `FolderRow` needed 9 of 40. Left alone this
is not just noise: it makes each file look like it depends on far more than it does, which
is exactly the impression a split is supposed to dispel. A small pruner (scratchpad, not
committed) removed specifiers whose identifier does not appear in the body.

**Allowlist 72 → 75**, and that is correct for a pure move: the ad-hoc chrome did not go
away, it changed address. Phase 2 deletes all four lines.

### Verified by hand

The row kebab is the highest-regression-risk piece in this file (per the rebuild plan) and
is not covered by tests. Checked in the browser: the menu opens with all six entries —
Load into scene · Download · Copy as path · Rename… · Move to folder… · Delete.

Worth recording how that check nearly went wrong: the first probe reported the menu did
not open, because it clicked the trigger twice and toggled it shut again. The DOM said
`aria-expanded="false"` and the portal was absent — both true, both meaningless. A probe
that fires a toggle needs to assert the state it produced, not the state it expected.

---

## StorageBrowser re-chrome (phase 2), and four small corrections

### Phase 2

119 palette classes across the storage files became semantic tokens. `StorageBrowser`
gained a `chromeless` prop so the dock draws the frame — one more box-in-a-box gone —
and `StoragePanel` is the shell's entry point. Maximize survives in both, because "give
this the whole window" is useful wherever the panel lives. **Allowlist 75 → 70.**

### One name per idea

`Library ▸ Storage ▸ "Storage" ▸ "Refresh file list"` said the same thing four times. The
panel is **Files** (its id stays `storage` — a persisted layout key), its internal `<h2>`
is gone because the dock header already names it, and the toolbar action is just
**Refresh**. Library is the place; Files is what is in it.

### Icons

**Undo and redo** were Lucide's `rotate-ccw` — a near-complete circle. At 16px that reads
as *refresh*, which is the worst possible neighbour for an actual Refresh button, and the
direction that distinguishes undo from redo was a few pixels of arrowhead on a circle.
They are now the flat hooked arrow every IDE uses: a bold horizontal arrowhead, then an
arc away from it. The arrowhead is the largest feature, so left-versus-right is legible at
a glance.

**Convert and Refresh were the same glyph**, sitting next to each other in the Library
toolbar. A circular arrow means "do that again"; conversion is a transformation between
two things. `ConvertIcon` is opposed arrows.

### The orientation gizmo was anchored to the wrong thing

It used `position: fixed`, which pinned it to the bottom-right of the **browser window**.
That was the same place as the bottom-right of the canvas back when the canvas was
full-bleed. The shell gives the 3D view its own grid area, so the gizmo ended up
underneath the right dock — glowing faintly through a translucent panel, and unclickable
where they overlapped.

`ThreeCanvas`'s container is already `relative`, so `position: absolute` anchors it to the
viewport region: unchanged in the classic full-bleed layout, correctly beside the docks in
the shell, and it follows a splitter drag for free — which a margin computed from the dock
width would not.

This is a specific instance of a general hazard worth naming: **`position: fixed` encodes
an assumption that the window and the content region are the same rectangle.** Every such
use is a latent bug once the app grows a layout.

### A bulk rewrite that changed meaning, not just appearance

The palette→token pass rewrote two entries of `GitHistoryPanel`'s `BRANCH_PALETTE` to
`bg-pass` and `bg-warn` — silently claiming that a branch hashing to slot 0 had "passed"
and slot 3 was a "warning".

That array is a **categorical scale**: its only job is that seven branches look like seven
different things. Semantic tokens are the wrong vocabulary for it, the same way they were
wrong for the theme preset cards. It keeps literal colours and the file keeps its
allowlist entry, now with the reason written above it.

The lesson generalises past this one array: a codemap from palette to semantics assumes
every colour *means* something. Colours used as identity — branches, categories, series in
a chart — mean only "not that other one", and a semantic token is a false statement about
them.

---

## M8 — cutover

Two commits: flip, then delete. Deliberately not one. The flip is the reversible half and
wants to be bisectable on its own; the deletion is a large diff whose review question is
"is anything still reachable", which is not a question you want mixed with "does the new
default work".

### What went

`Menu.tsx`, `InViewerPanelHost.tsx`, `OptionsComponent.tsx`, `SceneInfoBox.tsx`, the whole
`AppBody` tree, and the URL branch chain that chose between two UIs. `app.tsx` is 154
lines and does only routing. Allowlist 70 → 68.

### The fourth instance of the same trap

Deleting `AppBody` would have silently removed two things nothing else mounted:

* **`useUrlParamLoad()`** — every `?file=` / `?scope=` / `?derived=` deep link into the
  viewer.
* **`RestModeUI`** — the conversion-progress toasts and the upload context menu. A
  conversion started from the shell would have reported its progress nowhere.

That is now four times this rewrite has found bootstrap work hiding inside a component
the shell does not render — after the plugin top-bar regions, the legacy visibility flags,
and AuthGate. The shape is always identical: a hook or a mount with a side effect, living
in a *layout* component because that is where someone happened to be typing, invisible to
any search for "what does this app do at startup". **Neither would have thrown.** The
symptom is a feature that quietly is not there.

The lesson for anyone deleting a layout component: grep it for hooks and for components
rendered but never referenced elsewhere, *before* deleting it. Type-checking will not help
— removing the only caller of a side effect is perfectly well-typed.

### Two things the plan said to delete that survive, with reasons

**`useLegacyFlagSync`.** The plan reasoned that these visibility booleans existed only for
the classic UI, so removing the classic UI removes the need. That was wrong. They are
fields on two *business-logic* stores that their own panels read (`CellBuilderPanel` gates
on `panelVisible`, `SimulationDataInfoPanel` on `isPanelOpen`); the classic UI merely
happened to be what wrote them. Deleting the bridge leaves two docked panels rendering an
empty box with no error — the panel is mounted, it just decides not to draw. Removing it
properly means un-gating both panels and re-pointing external callers, which is work under
the business-logic fence and its own change.

**`?shell=0`.** Kept for one transition period so a regression has a workaround and can be
demonstrated side by side. The stored preference still wins over the new default, so
anyone who explicitly chose the classic UI stays there until they clear it: a default
changing underneath someone should not override a choice they made.

`embed/EmbedUI.tsx` also survives. It imports from `embed/`, not `src/`, so it was missed
by the import scan and only surfaced when `build:embed` failed — a useful reminder that
the three builds check different things. It now frames the shared `SceneBody` itself. It
is still a hand-rolled miniature of the shell, and replacing it with
`<AppShell profile="embed" />` is a rebuild, not a rename.

### A test that would have passed while asserting nothing

`regionCompat.test.ts` checked the shell mounts `AuthGate` by slicing `app.tsx` between
`if (useNewShell)` and `if (isAuthCallback)`. Cutover removed `useNewShell`, so
`indexOf` returned −1 and the slice became a prefix of the file — the assertion still
found `AuthGate` somewhere in it and passed. It failed here only by luck of ordering.

Re-anchored on the viewer branch's own markup, and it now asserts the anchor was found
before asserting anything about it. **A source-text test must fail loudly when its anchor
disappears**, or it degrades into a test of nothing at exactly the moment the code it
guards is being restructured.

### `frontend.md`

Rewritten. It was a 2023-era feature TODO describing a websocket GLB viewer, with
unticked boxes for things that shipped years ago. It now covers how to run the thing, the
fixtures, where the code lives, the presentation/behaviour split, the three builds and why
they fail differently, the test runner's explicit file list, and the known rough edges —
including the ones this cutover left behind.

---

## /convert and /admin become shell pages

They were reachable by URL and by a button in Preferences, and once you were there the
only ways out were the browser's Back button or editing the address bar. That is how a
page stops feeling like part of the application.

Both now render `<AppShell profile="page">` with the page filling the viewport track via
`viewportOverride` — the same slot the graph profile uses for ReactFlow.

**The constraint that made them separate is kept, not traded away.** They still mount
outside `AdaViewerProvider`, the profile still says `canvas: false`, and `CanvasWrapper`
stays behind `ViewportHost`'s `React.lazy` boundary — so the 3D scene, the websocket and
the tree never spin up, and three.js stays out of these routes' entry chunk in the
chunk-split build. Verified in the browser: `/admin` renders with no `<canvas>` in the
document at all. Only the dead end was given up.

Two profile changes were needed, and both are about *not* showing things:

* **`docks: false` for `page`.** The dock hosts render whatever the persisted layout says
  the current mode has open — which on these routes would be viewer panels reaching for a
  scene that was deliberately never mounted.
* **`menus: false`, a new profile flag.** Nearly every command acts on the scene, the
  selection or the layout, so a full menu bar here would be six titles of greyed entries;
  and outside the provider a command that reached for viewer state would do worse than
  no-op. The command palette travels with the menus for the same reason — they index the
  same commands.

The page bar is deliberately thin: who we are, what this page is, the scope, and the way
back. "Back to the viewer" is a real navigation rather than `history.back()`, because the
page may have been opened from a link or a bookmark with nothing behind it.

## Two corrections

**Greyed toolbar icons had unreachable tooltips.** A disabled button receives no pointer
events — that is in `BUTTON_BASE` and it is also what browsers do natively — so its
`title` never appeared. Every greyed icon was therefore unexplained, which defeats the
entire point of greying *with a reason*: the reason existed and could not be read.
`IconButton` now wraps a disabled button in a span that carries the tooltip and is not
itself disabled, so hover still works.

Worth generalising: **`disabled` is not just "cannot be clicked", it is "cannot be
interacted with at all"** — no hover, no focus, no tooltip. Any design that explains
itself on hover has to account for that, and the explanation is needed precisely when the
control is disabled.

**The "RIGHT DOCK" label is gone.** It was added to fill the header row once the tab strip
was hidden in the stacked arrangement. But it names the *container*, not the content, and
every stacked panel already carries its own header — filling an empty row is not a reason
to put something in it. `DOCK_LABEL` still does its real job in the accessible names.

---

## Universal tools belong in the universal place

Two reports, one rule: *a tool that applies in every mode is not a mode tool.*

**Section planes** was in the left rail **and** appended to every mode's strip except the
Library — the same tool in two places at once. It is in the rail only now. The Scene panel
also became `modes: "all"`, because clip planes, take-off, groups and mesh quality all
describe the loaded geometry, which exists in every mode; excluding it from the Library
meant the rail's own Section button had nowhere to open.

**Groups** was worse. Build's strip had a "Groups" button that opened the Scene panel's
*Tools* tab, while the groups it meant live under *Model* — a control that was both
mode-gated when it should not have been, and pointed at the wrong place.

Inspect's three buttons went the same way. They opened three different Scene-panel tabs,
which is three doors onto universal content dressed as mode tools. The rail now has **one**
Scene button, not four: the panel stacks its own groups into a column when it has the
height, so opening it shows all of them at once.

Where things ended up:

| | |
|---|---|
| **Rail** (every mode) | Fit · Focus · Hide · Unhide · Section · Measure · **Scene** · Undo · Redo |
| **Library** | Upload · Convert · Refresh |
| **Build** | Move · Rotate · Resize · Compile |
| **Inspect** | *(empty)* |
| **Results** | Play/pause · Legend · Data table · FEM concepts |

**Inspect is empty again, and that is the finding, not a gap.** It is the third time this
has come up. Inspect adds nothing because it is the base state: what it offers is the
*absence* of the other modes' apparatus. Padding the strip to make the mode look busy is
precisely what pulled universal tools into it the first time.

The test each entry now has to pass: *would this still make sense in a mode that has no
model / no results / no procedural document?* If yes, it is a rail or a menu item. The
remaining strips survive it — Move needs a builder selection, Play needs a result set,
Upload needs a scope.

---

## ?simfollow= becomes a shell window

The last standalone route. It renders `AppShell` on the `window` profile with
`SimFollowerPage` filling the viewport track — canvas-less like the pages, outside the
provider, no 3D and no websocket.

**It deliberately has no "Back to the viewer".** A follower is a pop-out belonging to the
tab that opened it, driving *that* tab's scene over the `ada-sim` BroadcastChannel.
Sending it to `/` would not return anywhere; it would quietly promote the follower into a
second full viewer — a second websocket and a second scene against the same session. So
`backToViewer` is a profile flag rather than something the reduced bar always shows: the
same chrome, two different truths about where "back" is.

The title earns its place. These windows get opened several at a time, one per source, and
until now they were indistinguishable in the taskbar. It now says *Following
&lt;source&gt;*.

`SimFollowerPage` also dropped its own `h-[100dvh]` wrapper for `h-full`: the shell's
viewport track sets the height, and a viewport unit inside a grid cell ignores its track
and overflows past the bottom.

All five profiles are now real. `app.tsx` is routing and nothing else.

## Disabled controls use the default cursor

`cursor-not-allowed` — the 🚫 — says *this action is forbidden*. Almost nothing in this UI
is forbidden; the controls are temporarily **inapplicable**, which is a much milder claim:
nothing is selected yet, no result set is loaded. Dimming plus a tooltip saying which
already carries the message, and a barred cursor on top of it is a scolding.

Changed across the design system and the shell (IconButton, MenuBar, MarkingMenu, Slider,
Checkbox, Switch). Reserved now for the genuine case — an action the user is not permitted
to perform. The admin tabs still use it; they have not been through the design system.

---

## The allowlist reaches 1

~1,900 palette classes across the 14 admin tabs and every other remaining file became
semantic tokens. **Allowlist 68 → 1.** The survivor is `GitHistoryPanel`'s
`BRANCH_PALETTE`, which is a categorical scale and documented as such above.

### Enumerating the mapping stopped working

Three hand-written passes each missed a fresh alpha variant — `bg-red-900/70`, then
`/40`, then `/60`, then `/50`. Tailwind's palette is a grid of *family × shade × alpha*,
so the mapping has to be **computed** from those three rather than listed. The generic
pass encodes what each family means in this product:

```
neutrals → surfaces / text / edges, chosen by shade
green    → pass        amber/orange → warn
red/rose → fail        sky/teal/purple → info
blue     → accent
```

with two rules that carry most of the meaning: a `bg` with an alpha **or** a very dark
shade is a tinted wash behind text (`-subtle`), while a mid shade is a solid fill; and
neutral `bg` shades map to surface depth, darkest furthest back.

**It is only valid because nothing left uses colour as identity.** Run over
`GitHistoryPanel` it would turn "branch #3" into "warning". That file was excluded by
hand, and any future use of this technique needs the same check first — a mapping from
palette to *semantics* assumes every colour means something, and colours used as identity
mean only "not that other one".

Colour is doing more work in the admin surface than anywhere else in the product — a run
passed, a worker is degraded, a token expires soon — so it is the surface that benefits
most from the tokens actually being semantic. It is also the first time the light theme
will work there at all: `bg-gray-900` is dark whatever the preset says, whereas
`bg-surface-0` follows it.

---

## Build gets real tools; Convert gets its own mode; dead chrome goes

### A correction

The cutover commit said `?shell=0` still reached the classic UI "for one transition
period". **That was false when it was written.** The same commit deleted the classic UI
and made `app.tsx`'s viewer branch unconditional, so nothing read the flag: the toggle in
the title bar set a preference no one consulted and navigated to a URL that changed
nothing. The escape hatch was described, not kept.

`shellPrefs.ts`, the pop-out toggle and the "new shell" badge are all gone. The badge had
the same problem in a milder form — it marked a UI that is now simply the UI.

### The Build strip

It had four buttons, all of which operated on a model you had no way to create from here.
Now:

**New procedural model…** first, because until you have one nothing else is usable. It
lived only in the Library's "+" menu — so the one place you would look while in Build mode
had no way to begin. It is now a shared action behind three doors (File menu, Build strip,
Library "+"), rather than three implementations.

**Add cell · opening · equipment · loft.** The placement ones arm a mode and show pressed
while armed; pressing the armed one disarms it, as Escape already did. Loft is a one-shot,
so no pressed state. These pass the mode-tool test: they are meaningless without a
procedural document.

**The Builder panel's own undo/redo are gone.** Two controls for one stack — differently
drawn, differently placed, and one of them only reachable while that panel happened to be
open. Undo is in the rail, where it is in every application anyone has used. Third
instance of the same duplication after section planes and groups.

### Convert as a mode

It was a panel in the Library's right dock, sharing the column with the file browser you
pick sources from — so choosing a file and choosing what to do with it competed for one
space. Converting is a different activity from browsing: you arrive with an intent ("get
this STEP into GLB"), not to look around.

`Library · Convert · Build · Inspect · Results` still reads as the work flowing left to
right. Convert mode keeps the file list on the left, because you convert a file you can
see, and gives the converter a 520px dock — a drop zone, a target matrix and a job list
stacked vertically do not fit a sidebar.

It also carried a "← back to viewer" link inside the panel, from its standalone-page days.
A link that navigates the whole window out of a panel you are sitting inside is exactly
the kind of thing that survives a move and stops making sense.

### Native `<select>` popups

The option list is drawn by the OS and does **not** inherit the `<select>`'s background,
so a themed picker opened onto unreadable options. Only styling `option` directly reaches
it. `surface-0` and not `surface-1`, because the panel surfaces carry alpha in the glass
presets and an OS popup composites against the desktop rather than the page.

### On the scope picker's placement

Asked whether it belongs in the title bar or in Library mode: it stays. Scope is
session-wide context — every file, conversion and job belongs to one — and it is the one
piece of state that changes what every other surface shows. It is also needed on the
`page` and `window` profiles, which have no Library to put it in. Library is where it is
most *used*, but persistent chrome is where it needs to be *visible*.

---

## Native dialogs replaced; the dev fixture stops lying about Build

Reported together, and they turned out to be two unrelated things wearing one symptom:
"New procedural model…" showed a dialog "in another layout", then failed with
`404 Not Found`.

**The layout was `window.prompt`.** The browser's own dialog, which is blocking,
unstyleable, and visibly not part of the application — a native prompt over a dark themed
viewer reads as a different program having opened. In the embed build it is worse: the
dialog is prefixed with the *host page's* origin, so a docs page would show
"docs.example.com says: Name for the new procedural model:", which reads as a phishing
attempt.

`ui/confirm.ts` grew from one question to three — `confirm()`, `promptText()`,
`alertText()` — all awaitable from non-React code, all rendered by the one host. The
prompt dialog submits on Enter, because the native prompt did: a replacement that loses a
behaviour the original had is how a "nicer" component loses the argument.

**The 404 was the dev fixture, not the feature.** It was wired up correctly; the local
REST stub implements no procedural routes at all. It now serves create / get / list, so
Build mode is reviewable without a backend.

That mattered more than it looks. A fixture that 404s a whole mode does not read as "the
stub is incomplete" — it reads as "this feature is broken", and the reasonable response is
to stop reviewing that mode. The error copy now says so explicitly: a bare 404 on this
endpoint gets "The server has no procedural-model API. In local development the REST stub
only implements files and scopes." A 404 alone sends people looking for a typo in the name
they typed.

### `tsc` does not cover the vite config

The first version of the fixture routes had broken regex literals — a heredoc had eaten
the `\/` escapes, leaving `/^/scopes/...`. `npx tsc --noEmit` passed, because
`vite.plugin-dev-rest.ts` is not in the tsconfig `include`; the only signal was the dev
server failing to boot with an esbuild stack trace and no message. Worth knowing: **the
build tooling's own files are outside the typecheck**, so a change there is verified by
running it, not by the gate.

---

## Enter accepts a transform — and what that exposed about Escape

Build mode had no key that said "this is right, I'm done". You dismissed a gizmo by
pressing Escape and hoping it kept your edit, or by clicking somewhere else and hoping
that did not also change the selection.

`Enter` now accepts the current operation:

* an axis-locked modal move **commits** (`endModalMove(false)` — the case where Escape
  genuinely differs, because it restores the cell);
* an active gizmo is simply put away, keeping the transform.

It is handled at the same fall-through point as Escape, so it is reached only after every
numeric entry flow has declined it — the loft, opening and extrude entries all consume
Enter and return, and none of them lose it. `Shift+Enter` stays compile-preview. With
nothing in progress, Enter is passed through rather than swallowed: the viewport is not a
form.

### Escape does not mean one thing

Reported during review and confirmed in the source. Escape's behaviour depends on *how
the move was started*:

| Started by | Escape |
|---|---|
| Dragging the gizmo widget | `setGizmoMode("none")` — **keeps** the transform |
| `G` then `X`/`Y`/`Z` (axis-locked modal move) | `endModalMove(true)` — **reverts** the cell |

`modalMove` is only ever set by `startModalMove`, which is the keyboard path. So the same
key cancels or confirms depending on which of two routes you took to the same operation —
which is precisely why it was unclear what Escape did.

The universal convention (Blender, Maya, C4D, every CAD tool) is Escape cancels, Enter
confirms. Escape is now half of that. **Making it consistently cancel is a real behaviour
change** — it means reverting a widget drag whose edit is already coalesced onto the undo
stack, so it is un-doing something the user may currently expect to keep. That belongs in
its own change, with the decision made deliberately rather than as a side effect of adding
Enter.

---

## The fifth silent loss, and a test for the class

Right-click did nothing in Build mode. `Menu.tsx` rendered four cellbuilder overlays —
the context menu, the port menu, the insert-equipment menu and the **gizmo HUD** — and
the cutover deleted it. Nothing threw: the controller kept opening menus into a store
nobody was rendering, and the only symptom was that right-click stopped responding. The
gizmo HUD is the numeric entry field, so the Enter-to-accept work landed against a HUD
that was not on screen.

They are mounted in `OverlayLayer` now, gated on an open procedural model.

**Five times** this rewrite has lost something to the same shape: the plugin top-bar
regions, the legacy visibility flags, AuthGate, `useUrlParamLoad` + `RestModeUI`, and now
these four. Always a component that is *rendered* somewhere rather than *imported*
somewhere useful, so deleting its host leaves no dangling reference and no type error.

`mountedOverlays.test.ts` now asserts that every such component is both referenced **and
rendered** (`<Name`) somewhere under `src/shell`, and that `useUrlParamLoad` is *called*
rather than merely imported. It checks its own anchors resolve before asserting — the
lesson from `regionCompat`, which quietly became a test of nothing when the string it
sliced on disappeared.

### Two things this exposed downstream

**The marking menu opened over the converter.** It gates on the event being inside
`[data-testid='viewport-host']`, and Convert mode's overlay is *inside* that host — so
right-clicking a file-conversion form produced a radial menu of camera and selection
actions for a model that was not even visible. The overlay is tagged
`data-viewport-overlay` and the menu now declines when the target is inside one:
"in the viewport" and "over the 3D" had silently stopped being the same thing.

**"Unhide all" was renamed in three places out of four.** The rail, the command palette
and the shortcut registry got "Show all"; the marking menu kept the old label, because it
builds its items from its own list. Worth noting as a smell — the label is data in four
registries rather than one — though consolidating them is its own change.

---

## Preferences becomes Settings — a dialog, not a panel

It read **"Show preferences"** in the File menu, and that label was a symptom rather than
a typo. Panel commands are generated as `${isOpen ? "Hide" : "Show"} ${title}`, which is
right for something you park beside the model and meaningless for a destination you open,
use and close. Being a panel also meant it inherited the panel theme — so on the default
glass preset the settings were **translucent over the 3D view** — and that it competed for
dock space with panels you actually want open while working.

It is a dialog now, shaped after PyCharm's Settings: search top-left, categories down the
left, the selected page on the right. That layout scales; a single scrolling column of
disclosures does not, and this already has five groups.

`Scene · Theme · Performance · Conversion engine · Account & scope`, with the build
identity in the footer. `Shift+Q` opens it. `File ▸ Settings…` — with the ellipsis, which
is the convention for "this opens a window".

**Search is category-level, and that is a deliberate subset.** PyCharm searches actual
setting labels because every setting there is declared data; ours are JSX, so a true index
would mean restructuring every option first. Category keywords name the settings people
would actually type ("antialias", "point size", "dpr"), which gets you to the right page —
most of the value — and `matchCategories` requires *every* word to match, so a two-word
query narrows rather than widens. The weakness is that the keyword lists are
hand-maintained and will drift; the fix is expressing options as data, which is its own
change.

The `preferences` panel is gone from the registry, and `panelRegistry.test` records why so
the next person does not "restore" it.

## Compile in the dev fixture

`Compile` reported `previewProceduralModel(...) failed: 404 Not Found`, which reads as a
broken feature. It is not: compiling runs a worker and produces a GLB, and there is
nothing honest a static fixture can return.

It now answers **501 with the reason in the status text** — "compiling needs a worker -
the dev REST stub has none". The status line is the only channel available: `ApiError`'s
message is built from `${what} failed: ${status} ${statusText}` and drops the JSON detail
entirely, so a body would never have reached the user. Worth remembering when adding
fixture routes: the reason has to go in the status line, not the body.

---

## The Builder panel splits: the document on the left, its settings on the right

The panel "had a lot going on" because it held two different kinds of thing in one narrow
column: **the model** (its cells and equipment) and **settings about the model** (grid and
snapping, compile settings, groups).

Cells & equipment is now the **Model** panel in Build mode's left dock — where a model
tree belongs, and where the Outliner puts the same idea for loaded geometry. As a whole
panel rather than a collapsed disclosure it can also fill the height, instead of capping
at 14rem and scrolling inside a section that itself scrolls.

**The Outliner is deliberately not opened alongside it.** In Build mode the model tree
*is* the outliner; the loaded-GLB tree answers a different question you ask occasionally,
and it is one menu item away. Two trees side by side answering nearly the same thing is
the duplication this rebuild keeps removing — and `layoutStore.test`'s "defaults are
sparse" guard caught the first attempt, which opened four panels.

Build mode now reads: **toolbar** = what you do, **left** = what the model contains,
**right** = how it is built, **viewport** = the model.

### The Settings dialog stopped changing size

Clicking between pages grew and shrank the window — Performance is twice the height of
Theme — so it jumped under the pointer and the category you were aiming at moved. Fixed
height, scrolling content pane. A window you navigate around has to hold still while you
do it.

---

## Results: the transport moves to the toolbar

The Simulation panel had its own row of Play / Stop / data-panel buttons, and Play and the
data-panel toggle were *also* in the Results mode toolbar. Two play buttons for one
playback state, differently drawn and differently placed — the third duplicated control
group found this way, after section planes and groups.

The split that resolves it: **the panel keeps the controls that pick a value** (field,
step, colormap, deform scale, warp factor); **the toolbar takes the ones that do
something** (play/pause, stop, legend, data table, FEM concepts).

The gear stays in the panel, because what it reveals is that panel's own options row — a
disclosure for the panel, not an action on the scene.

`stopPlayback` had to reproduce three steps rather than one, and the reason is worth
recording: the RAF driver only applies the deformation factor **while playing**, so a stop
that merely zeroed the store would leave the mesh frozen at whatever deflection it was
showing. The numbers would say zero and the model would disagree. It pauses, zeroes the
factor, zeroes the mesh's morph influence, and resets the driver's phase — the same four
steps in the same order the panel's Stop did.

It reaches the mesh through the already-exported `getActiveFeaMesh()`, so nothing under
the business-logic fence had to change.

---

## Clip tools: same row, to the right, toggled from the rail

The rail's Section button used to open the Scene panel's Clip tab — a whole dock column
given up to reach three buttons. It now toggles a group of clip tools into the **mode
toolbar, appended to the right of the mode's own tools**.

Appended, not substituted, and that is the point: **clipping is a second activity layered
on top of whatever mode you are in.** You clip a model you are inspecting, or one you are
building. Replacing the row would have implied the mode had changed, which it has not —
the same reasoning that made Convert its own mode says clipping is *not* one.

`Clip on X · Y · Z │ Flip │ Drag handle │ Remove all`, each greyed with a reason until it
applies ("No section plane yet", "No plane selected"). The divider only appears when the
mode actually has tools to divide from — Inspect's strip is empty, and a rule against the
left edge reads as a rendering fault.

Putting the tools away also hides the drag gizmo. Leaving a manipulator on screen after
its toolbar is gone strands a control with no visible owner: you can still drag the plane
and nothing on screen explains what you are dragging.

The Clip tab still exists for the plane **list** and the cap colour — the things a toolbar
cannot hold. Both drive `sectionStore`; the toolbar is a second entry point, never a
second implementation.

### The axis glyphs, twice

First attempt drew a detailed plane with the axis letter beside it. At the 16px these
actually render, the letter came out around 6px and all three buttons were
indistinguishable — the glyph had spent its pixels on the part that is the *same* across
all three. The axis is the entire difference between them, so it now gets the space: a
slab with a large letter on it.

Same failure as Convert and Refresh sharing the reload arrow, and worth stating as a rule:
**an icon set has to spend its pixels on what differs between its members**, not on what
they have in common.

---

## Library stops being a mode; Files becomes a flyout

**Browsing files is not an activity you switch into.** It is something you do briefly, in
the middle of another activity, to open the thing you are about to work on. Making it a
mode meant leaving whatever you were doing to go and find a file — backwards for the one
action that *starts* most sessions.

Modes are now `Convert · Build · Inspect · Results`, and Files is a **column of its own
between the rail and the left dock**, toggled from the top of the rail.

Its own column, and not a dock tab, for a specific reason: sharing the left dock with the
Outliner means opening Files *hides the model tree you were reading*. They answer
different questions — "what exists on the server" and "what is in this scene" — and you
often want both. A separate track pushes rather than replaces, and the canvas reflows as
it always does.

This is the activity-bar pattern: a strip of icons revealing a panel beside itself, which
is what PyCharm's tool windows and VS Code's sidebar both do.

Convert opens it on entry, because you convert a file you can see — but only on
*entering*. Closing it then keeps it closed: a panel that reopens itself is a panel you
cannot dismiss.

### Three smaller things it dragged in

**The scope picker moved into the Files panel.** It was in the title bar, as far from the
file list it governs as the window allows. Scope decides which files *exist* — upload
under one and they are invisible under another — so it belongs at the top of the list it
filters, the way a folder path does. The title bar kept it visible everywhere, but
"visible everywhere" is worth less than "next to the thing it changes", and Files is now
reachable from every mode.

**The two big blue buttons.** Add and Refresh were accent-filled 40px squares, so a panel
header showed two large blue blocks — putting "add a file" and "refresh" above the files
themselves. They are ghost buttons now. The 40px touch floor still applies under a coarse
pointer; that is what the size classes already do.

**A storage glyph, not a folder.** A folder means "a directory on my machine". This is a
scoped remote store you upload to and convert from, and the panel also lists procedural
models and CI artefacts, which are not files in a folder sense.

### A test that would have proved nothing

Removing `storage` from the registry orphaned the "runtime-gated panels resolve to null"
test, and the obvious repair — point it at `admin` — was wrong: admin is gated on
`isRestMode() AND isAdmin`, so it would have been null in *both* halves and the assertion
would have passed while testing nothing. It uses `convert`, which is REST-gated only.
Second time this session a test nearly degraded into a tautology during a refactor.

---

## Modes filter the lists, not the scene

Each mode now lists the models it is about: **Build** the procedural model, **Results**
the ones carrying results, **Inspect** everything.

**The Outliner only. Nothing is hidden from the 3D view, and nothing is unloaded.**

That was a deliberate choice between two designs. Filtering the *scene* is what "mode"
means in some DCC tools, and it would be defensible — but a model that silently vanishes
on a mode switch leaves its reason off-screen, and "my model disappeared and I do not know
why" is a worse problem than the one being solved. It would also break the non-modality
contract in `modeStore.ts`, which says a mode changes what is **offered**, never what is
loaded or visible.

Filtering the list keeps the contract and still gets the benefit: in Results you see your
result sets, not the eight geometry files you happen to have open.

Three rules in `outlinerFilter.ts`, all tested:

* **Procedural beats result.** A compiled procedural model can carry results; while you
  are *building* it, the fact that it is your model matters more than that it has been
  analysed — otherwise Build would stop listing the very thing you are editing.
* **It never filters down to nothing.** An empty Outliner in Results, while models *are*
  loaded, reads as "the tree is broken". The filter is a convenience, not a rule worth
  enforcing against the only thing you have open.
* **An unknown mode lists everything.** Failing open matters: a mode added later without a
  rule should show the user their models, not an empty tree.

When rows *are* filtered, the panel says so — "N more loaded — show all" — and the toggle
is one click. A list that quietly drops rows is indistinguishable from one that failed to
load.

**Verified by tests, not by eye.** The dev fixture loads a single model, so the
never-filter-to-nothing rule always fires and the filter cannot be observed in the browser
with it. The 14 unit tests cover the classification and the fallbacks; the visible
behaviour with several models loaded has not been exercised by hand.

## Builder View tab dissolved; Mesh tab scoped to Inspect/Results

The Builder panel's **View** tab is gone, split three ways by asking what each control
actually was:

- **View state** (representation topology/simulation/detail, superimpose, side-by-side,
  port overlay, recentre) was never panel content — it is seven commands. They now live
  in `buildActions.ts` + `commands.ts` and surface under **View ▸ Builder**. Menus have
  no pressed styling, so `ACTIONS` gained `checked`/`checkedTitle`: the title renders as
  `✓ Topology` when the state is on. Without that a menu of toggles gives no feedback at
  all about which one is active.
- **The two compile-output toggles** (`buildSim`, `buildDetail`) moved into BuildTab's
  Compile settings. They were mis-filed: they control what the compiler *emits*, not
  what you *look at*.
- `ViewTab.tsx` was deleted and de-registered.

The Scene panel's **Mesh** tab is now mode-scoped. `TAB_META` gained an optional `modes`
field and `tabsForMode` applies it alongside the existing contextual gate. Mesh quality
asks whether a discretisation is good enough to trust — Inspect and Results work. In
Build you are authoring the geometry the mesh will later be made *from*, so there is
nothing to assess yet.

**The `?worker&inline` barrier again, fifth time.** The new `sceneTabs.test.ts` imported
the rule from `SceneBody`, which reaches a store, which reaches the model worker, which
only a bundler resolves. Same `does not provide an export named 'default'` as every
previous occurrence. Fix is always the same: extract the pure rule to `src/shell/`
(`sceneTabs.ts`), have the component re-export it. Assume any rule worth testing must
start life outside a component.

## Files header aligned with the dock panels

The Files header's buttons were hand-rolled at `min-h-[40px] min-w-[40px]` with 24px
icons, sitting one column away from Model and Outliner whose headers use `IconButton
size="sm"` at 22px. The mismatch was visible side by side. They now use the same
`IconButton`/`Icon size="sm"`.

**Maximize was replaced by Close.** Maximize made sense when Storage was a floating panel
over the 3D view. As a resizable column with a splitter it is redundant — you widen it by
dragging — and it made Files the one panel whose header offered no way to put it away.
Removing the button made the whole `maximized` machinery dead (nothing could set it
true), so the state, its Escape handler, the fixed-overlay styling branch and the
body-portaled scrim went with it: ~40 lines.

Two things had keyed off `maximized` as a proxy for "wide": the Modified column and the
list's fill behaviour. The column now keys off the panel's real width
(`useFilesPanel.width >= 420`) — it is a space question, and the user answers it by
dragging the splitter, rather than by entering a mode that no longer exists.

## "Files" is now "Storage", and its header mirrors the dock tab strip

The panel was labelled Files in the rail while calling itself Storage inside, and the
API, the scopes and the docs all say storage. One name now: **Storage**.

Its header was still built its own way — a title-plus-dropdown row of no fixed height,
sitting one column away from Model and Outliner whose headers are a 32px strip with an
icon+label chip on the left and controls on the right. In the flyout it now uses that
same shape exactly (`h-8 px-1 border-b border-edge`, `gap-1.5` icon+label), so the two
bars share a baseline and a bottom rule — measured identical at top 73, height 32.

The scope picker moved out of the title line onto its own row underneath. A dropdown
wedged into a title bar is what made the header a different height and shape from every
other panel's; below the title it reads as this panel's folder path, which is what it is.

Refresh was the last unaligned icon: a bare `ReloadIcon` at its natural size next to two
16px `Icon`s. All three header icons now measure 14px.

The store, its key (`ada:files-panel:v1`) and the module name stay `filesPanel` — renaming
the persisted key would silently reset every user's panel width and open state, which is
a real cost for no gain that the user can see.

## Add opening / Add equipment are split buttons

Both need a *type* before placement means anything, so the first toolbar version made the
whole button open a type picker. That made every placement cost two clicks and a menu,
including the tenth identical door — a toolbar button that never actually does the thing
it is named after.

They are split buttons now: the icon half fires with the chosen type, a 14px caret beside
it opens the picker. The tooltip names what will be placed (`Add opening: Door (db)`), so
the current type is readable without opening anything.

Three rules, in `src/shell/splitButton.ts` and tested there:

- **A type is chosen** → fire.
- **Nothing chosen yet** → the icon half opens the picker too. Arming to place "nothing"
  is a press with no visible effect, which reads as a broken button.
- **Already armed** → always fire, because a second press disarms. Offering a type picker
  to cancel something answers a question nobody asked.

`chosenTypeLabel` returns null for a slug that is no longer in the catalogue — a model can
be reloaded against a different one while a stale slug sits in the store, and a button
that claims it places a Door and then places nothing is worse than one that admits no type
is chosen.

Two details that are easy to get wrong:

- **The pair needs its own flex box.** The toolbar has `gap-0.5`, so without a wrapper the
  2px gap lands *between* the halves and they read as two adjacent buttons — exactly what
  the squared-off facing corners are trying to deny.
- **The first version built the wrapper as a component defined inside the render loop.**
  A fresh function identity every render means React remounts the subtree, dropping the
  button refs the menu anchors to. Branch on the element, never on a locally-defined
  component type.

`caretClasses()` joins the design system rather than `buttonClasses(...) + "w-3.5"`: the
size classes carry horizontal padding, and `cn` is a plain join, so two conflicting
padding utilities are resolved by stylesheet order rather than by the order written. This
is the third time that has bitten (`w-20` on `Input`, the `Slider` wrapper).

## One scope picker, in Storage

The title bar kept a copy of the scope dropdown after the Storage panel got its own. Two
controls bound to the same store always agree, so the second one teaches you nothing and
still has to be read and ignored. The title-bar copy is gone.

Scope lives where its consequences are: it decides which files exist — upload under one
and they are invisible under another — so it belongs at the top of the list it filters,
the way a folder path does.

The cost is real and worth stating: scope is now only visible while the Storage panel is
open. That is the right trade because scope only matters when files do, but it does mean
the answer to "which project am I in?" moved behind a toggle.

The **page profile keeps its copy**. `/convert` and `/admin` have no rail and no Storage
panel, so removing it there would leave scope with no home at all rather than a quieter
one.

## The type catalogues 404'd in dev, not on this branch

The + Opening and + Equipment pickers showed "No opening types". The cause was not the
toolbar rewrite: `fetchOpeningTypes` / `fetchEquipmentTypes` fire from `openModel` in
`cellBuilderStore`, byte-identical to main, and main's panel has the same empty-list
fallback. Every procedural catalogue endpoint simply 404'd against the dev REST stub,
which also printed eight warnings on every model open.

The stub now answers seven of them: opening, equipment, cell and system types, design
rulesets, engines, and the equipment resync. Everything is tagged `origin: "code"` —
the real API returns the union of code-defined archetypes and the scope's DB entries, and
the code half genuinely is a static list, so the fixture is the honest half rather than an
invention. Tagging anything `"catalog"` would put rows in a database that does not exist
here, and every picker label shows the origin ("Door, single leaf (code)"), so the label
would be lying.

`blueprints` and `detailing-engines` answer with empty lists, and compile still answers
501. Those need a worker; there is nothing truthful a static fixture can return.

**Heredocs ate the regex escapes for the third time** (`\/` → `/`), which produced
`/^/scopes/...` — a broken regex that stopped vite reloading the config, so the endpoints
kept 404ing after the "fix". Use the Edit tool or write a script file; never a heredoc for
anything containing backslash escapes.

## Storage's dialogs are the app's dialogs

Deleting a procedural model asked through `window.confirm`. Thirteen native dialogs were
left in `StorageBrowser`: four deletes, the template-name prompt, and eight alerts for
failures.

They are blocking, unstyleable, and visibly not part of the application — a browser dialog
over a dark themed viewer reads as a different program. In the embed build it is worse:
the dialog carries the HOST page's origin, so a docs page shows "docs.example.com says:
Delete file?", which looks like a phishing attempt. All thirteen now go through
`confirm()` / `promptText()` / `alertText()`.

Two things the conversion improved beyond the chrome:

- **Multi-line messages became real lines.** `previewKeyList` returns a newline-joined
  string that a native dialog rendered as separate lines; in a styled `<p>` it would wrap
  mid-path. It is split into one body line per key.
- **Alerts got titles.** `window.alert(e.message)` gave a bare string with no indication
  of what failed. "Some files could not be moved" over the list says more than the list.

`noNativeDialogs.test.ts` enforces this with an allowlist burn-down, same shape as
`noAdHocChrome`: the seven admin tabs are all that remain, and a second test fails if an
allowlist entry has already been converted — a burn-down list holding converted files
stops measuring anything and quietly re-permits what it names.

## The Scene panel's Clip tab is gone

Section planes are a rail tool with their own strip, so the tab was the same controls a
second time — the fourth duplicated group found this way, after section planes in the mode
strips, groups, and the Results transport. Two places to add a plane means neither is
*the* place.

Folding it away needed more than deleting it. Add / flip / gizmo / clear were already
toolbar buttons, but two things in the tab were not, and would have gone silently:

- **Which plane you are steering.** Flip and the gizmo act on the *active* plane, and with
  several planes there was no way to say which that was.
- **The position slider.** Dragging a plane along its normal without reaching for the 3D
  gizmo is the thing people actually did in that tab.

Both became one control — `SectionPlaneControl` — rather than two: the button names the
active plane and switches between them, the slider moves it. They belong together because
a slider with no label is meaningless in a strip that has no room for one. It renders
nothing at all when there are no planes, rather than showing a dead slider next to the
three buttons that create what it needs.

`ModeTool` gained a `render?: () => ReactNode` escape hatch for this. Deliberately narrow:
a strip of icons cannot express "choose among several of the same object" or "set a
continuous value", and those are the only two cases. A third would be the signal to
promote it into a real toolbar-widget type instead of adding a fourth.

**Cap colour went to Preferences, not the strip.** It is a look you pick once, not a
per-cut action, and a colour well in a toolbar of verbs is the kind of thing that ends up
there because it had nowhere else to go.

The arithmetic moved to `src/shell/sectionRange.ts` with tests. Two things worth pinning:
`position` and `constant` are sign-inverses, and getting that backwards makes the slider
drive the plane the wrong way — visible only in 3D, never in a type. And the range is
padded 10% past the box at both ends, without which the extremes leave the plane exactly
touching the model, so it can never fully clip or fully reveal; the leftover sliver reads
as a broken slider.

The marking menu's "Section planes" entry now arms the clip strip instead of opening a
Scene tab that no longer exists.

## The Simulation panel is plugin-only now

Its built-in content had already been leaving for the toolbar — play, stop, the legend and
the data table went first. What stayed was everything that picks a *value*: field,
component, step, deformation scale, colormap, layer, IP reduction. That left a permanently
docked panel holding one column of dropdowns, spending a quarter of the window on controls
you set once and then leave alone.

Those moved into a **display popover** on the Results toolbar. A popover is the honest
shape for that content: on screen while you set it, gone afterwards, with the 3D getting
the space back. It is not a panel demoted — it is the same controls, hosted where their
lifetime actually is.

**The panel stays registered.** Plugin `fem-sidebar` panels have no other host, so deleting
it outright would drop them silently — inventory row B11 again, the same class of failure
as the four cellbuilder overlays lost with `Menu.tsx`. It is now `available:
hasSimulationContributors` and `defaultOpen: false`, so a stock Results layout has no
Simulation panel and a plugin install still gets one.

Two things the move exposed:

- **The transport was FEA-only.** `togglePlay` and `stopPlayback` required a live FEA
  session, which greyed them out for a GLTF model whose clips were perfectly playable.
  Invisible while the panel carried its own play button; with the panel gone it would have
  meant no way to play a clip at all. They now fall through to the mixer, and `stop` zeroes
  `currentKey` too — the scrubber reads its position from the store, so a stop that only
  told the mixer would leave the slider parked where the clip stopped.
- **A fifth duplicated control group.** The GLTF path had its own play / stop / data
  buttons, drawn differently from the toolbar's. Gone; the clip picker and time scrubber
  stay, because those choose a value.

The clip picker was also the "No animation" dropdown that was unreadable: a fixed `w-60`
with no `min-w-0`, in a row built for a panel as wide as the window, so in a narrow dock
everything competed for one line and the clip name — the thing you actually read — got the
least of it. It is a labelled column now.

**Two traps hit again, both already in this document.** The popover drew transparent, with
the Storage header showing straight through the controls: panel surfaces carry alpha in the
glass presets and CSS cannot flatten it, so it needs an opaque `surface-0` base with the
tinted layer stacked on top. And `panelRegistry` importing the `@/plugins` barrel pulled in
the slot components, which reach stores, which reach the model worker — every test touching
the registry died on `?worker&inline`. Sixth occurrence. Import `@/plugins/registry`
directly.

## The Builder's Tools tab is "Output" now

It had become a bag of five buttons with no subject: Resync equipments, Propose
relocations, Export to Excel, Download IFC, Download Genie XML — plus a CAD checkbox, and
underneath them the things those actions produce.

Split by what each thing *is*, the same cut that dissolved the View tab:

- **Export** is a split button in the Build toolbar, with the format on the caret. Same
  shape as the opening and equipment pickers, for the same reason: exporting the same
  format twice should not cost a menu.
- **Resync** and **Propose relocations** are commands under **Tools** in the menu bar.
  They are occasional and deliberate, and both are named far better by a sentence than by
  an icon nobody would recognise. The IFC-CAD toggle sits with them as a checked command,
  because it changes what the export *produces*, not what you see.
- **What is left** — the relocation proposal you read and then accept, the resync summary,
  the compile log — stays in the tab, which is why it is called Output.

Two things worth recording:

- **`exportFormats()` asks the engine.** Only `adapy-default` compiles a detail or
  simulation model; the others export the workbook alone. The panel did that check inline
  as a JSX condition, which is exactly the kind of rule that evaporates in a move — a
  toolbar offering a Genie XML the engine cannot produce would fail at the worker, not at
  the button. `chosenExportFormat` also returns null for a format the current engine
  cannot make, so switching engines cannot leave the button claiming an IFC it cannot
  produce.
- **The chosen format lives in `src/shell/exportPrefs.ts`, not on `cellBuilderStore`.**
  The store is business logic and off limits to this rebuild, and this genuinely is chrome
  state: it remembers which item of a split button you picked last. The model does not
  care.

`splitButtonState` gained a `noun`, because Export picks a *format*. Hardcoding "type" had
the export button saying "choose a type" — the kind of wrong word that makes a control
read as somebody else's, pasted in.

The tab also carried six dead imports (`PositionedMenu`, `typePickerItems`, `followerUrl`,
`IconOverlaySection`, `Section`, `describeToolState`) left by the earlier split, when
snapping, the follower window and the icon overlay moved out and took their usages with
them. TypeScript does not flag unused imports here, so they had simply sat there.

## No native dialogs left anywhere

The admin tabs were the last seven files on the `noNativeDialogs` allowlist. It is empty
now: twenty-two `confirm` / `prompt` / `alert` calls across nine admin files, plus two in
`WebsocketStatusMenu`, all converted.

**The test was wrong, and finding that was the point.** Its regex matched
`window.confirm(…)` only. A bare `confirm(…)` is the *same global* opening the *same
dialog* — and six of them sat in `CorpusTab`, `StorageTab`, `ProjectsTab` and
`CliTokenButton` while the test reported those files clean. A rule that catches only the
spelling people happened to use is not a rule. It now matches both, with an `EXEMPT`
pattern for our own awaited replacements (the native ones are never awaited — they block)
and a skip for `{/*` JSX comments, which is what made `AppShell` a false positive.

Three of the conversions needed their enclosing function made `async` — `deleteFolder` in
CorpusTab, `onFolderRenameOrMove` in StorageTab, and the CI-bot-token handler in
ProjectsTab. That is the tell that these were never really synchronous decisions: the code
was written around `confirm` blocking the thread, which is the behaviour that makes it
wrong in the first place.

The copy improved on the way through. `window.confirm` takes one string, so everything was
crammed into it with `\n\n` separators — the key preview in CorpusTab's bulk delete, the
scope name in StorageTab's cache clear. Each is now a title and separate body lines, which
is what they were trying to be.

## The legacy flag bridge is gone, and it inverted

`useLegacyFlagSync` mirrored dock state into the old visibility booleans, because panels
gated themselves on them: in the classic UI those flags *were* the visibility model, so a
docked panel without its flag set rendered an empty box. It was always meant to come out
at the cutover.

Deleting it was not enough, because the intent underneath is real. `cellBuilderStore` sets
`panelVisible: true` from `openModel`, `focusSystem` and `revealEquipment` — not as "this
panel is visible" but as **"the user just did something that needs the Builder on
screen"**. Dropping the flag outright would have made "focus this system" quietly do
nothing whenever the panel happened to be closed.

So the direction inverts. `usePanelReveal` watches the flag going *true* and opens the
dock panel; the panel no longer gates itself; the dock decides visibility as it does for
everything else. The store asks, the shell answers — which is the right way round, and
half the code.

**Rising edge only.** Acting on `false` would close the Builder out from under you when
the model closes, instead of letting it say so. Same reason mode switching never unloads
anything.

`fea-table` turned out not to need any of this: nothing read `isPanelOpen` any more, only
wrote it hoping something would notice. Its two writers — Properties' "Show in data" and
the marking menu — now call `openDataTable()` directly, one step where the flag was three
(set a boolean, have a bridge notice, have the dock follow).

`panelSelfGating.test.ts` pins all of it, as source text rather than renders: the failure
it guards is a line someone adds back at the top of a panel body, and no render test
catches that without arranging the exact state that hides the panel.

## EmptyState

Every panel had grown its own, and they agreed on nothing — some centred, some flush left,
some 11px, some 13px. Worse, most said only half of what an empty state is for.

The shape is now fixed: **`title`** says what is missing, as a statement; **`hint`** says
what to do about it, *naming the actual control in the words the control uses*. The hint
is the half panels kept leaving out, and it is the half that matters — "Nothing selected"
describes a state the user can already see. And a hint that paraphrases ("start a new
model" for a button labelled "New procedural model…") sends people looking for something
that is not there, which is why `<Ui>` exists to quote control names verbatim.

The Builder panel gained one in the process. It returned `null` when no model was open —
a docked panel drawing nothing, which is indistinguishable from one that crashed. That is
the same complaint that removed the visibility flag two paragraphs up, so it would have
been odd to leave it in place.

## Saved workspaces

The store has had `workspaces`, `saveWorkspace`, `loadWorkspace` and `deleteWorkspace`
since M2, tested, and reachable from nothing. A feature that exists only in a store is not
a feature — it is a plan someone stopped halfway through.

They are commands now, under **Window**: save, one entry per saved arrangement, and forget.

**Saving captures every mode, not the current one.** A workspace is how you like the
application set up; one that changed only the mode you happened to be in would be a
per-mode preset wearing the wrong name — and `resetMode` already covers that.

**`menuModel` gained a `group` item kind.** Workspaces are named by the user, so there is
no id to write into the menu structure; a group names a command-id *prefix* and expands
from the live registry at resolve time. That keeps `menuModel.ts` what it is — a
description of where things go, not a copy of what exists. It inherits the submenu rule:
matching nothing leaves no empty section behind, because a title you can click for nothing
is worse than no title.

`menuCommandIds` cannot check a group — it names no id — so a test asserts the Window menu
actually carries `layout:workspace:`, and that the prefix matches as a prefix rather than
a substring.

Overwriting asks first. Losing an arrangement you spent time on because you reused a name
is exactly the kind of thing a confirm exists for.

## The shortcuts reference had already drifted

`docs/SHORTCUTS.md` is generated from the registry by `npm run gen:shortcuts`, and the
generated file is committed. Nobody had run it since "Unhide all" was renamed to "Show
all", so the published reference promised a command by a name the product no longer used.

A generated file that is committed but never checked is a hand-written file with extra
steps: it goes stale just as fast, and more quietly, because everyone assumes the
generator ran. `shortcutsDoc.test.ts` regenerates in memory and compares. It never
writes — a test that fixes the thing it checks reports success forever and tells you
nothing.

It also asserts the parse still finds shortcuts *at all*. The generator reads the registry
as text, so a reformat of those object literals would match nothing — and an empty
reference would otherwise sail through the comparison by matching an equally empty
regeneration.

## This document was being written to the wrong file

Nine entries from this session went into a new `docs/UI_BASELINE.md` at the repository
root, while the real one — 2000 lines, referenced by `frontend.md` — is
`src/frontend/docs/UI_BASELINE.md`. Merged back, and the stray root copy is gone.

Worth recording because of how it happened: `frontend.md` says "see `docs/UI_BASELINE.md`",
which is correct *relative to `src/frontend`*, and a repo-root path of the same name was
plausible enough that nothing ever looked wrong. The notes were fine; they were just
orphaned from the document they belonged to.

## The rail is customisable — show and hide, not reorder

Right-click a rail button to hide it, right-click the rail for the full list, or open
**View ▸ Customise the tool rail…**. The last M7 item.

**Deliberately not reorderable.** The rail is grouped — camera, then visibility, then
history — and the dividers are a claim about what belongs with what. Dropping Undo between
Fit and Focus would break that claim while leaving the rules that draw it in place. It
also protects the promise the rail is built on: a tool means the same thing in every mode
and sits in the same place. A rail you can shuffle is one where nobody's muscle memory
transfers, including from a screenshot in the docs. So the arrangement stays and the
contents are yours.

Three rules, all in `railArrangement.ts` with tests, because they are the only part with
any reasoning in it and all of them are invisible until they go wrong:

- **Dividers get tidied.** Hide both tools in a band and its rule would sit against the
  one above it; hide the first tool and a rule floats at the top. Two rules in a row is
  not a group boundary — it is a rendering fault to anyone who did not do the hiding.
- **The last tool cannot be hidden.** An empty rail is indistinguishable from a broken
  one, and the control that puts it back is in a menu you now have no reason to believe
  exists. Same reasoning as the outliner's never-filter-to-nothing rule. The dialog says
  so, rather than leaving a checkbox that stopped responding.
- **Storage is essential and not offered.** Hide it and the only route to opening a file
  is the File menu — true, but the rail is where people look, and a rail with no way to
  open anything reads as an application that cannot open anything.

**Prefs store the hidden set, not the visible one.** A tool added in a later release is in
nobody's saved list; with a visible-set it would be invisible to every existing user, with
no error anywhere to explain a feature that shipped and cannot be found.

`registerRailTools` passes the list from `ToolRail` to the dialog rather than the dialog
importing `RAIL_TOOLS`, which would make the two files import each other — and an import
cycle resolves differently across the three builds.

Two things fixed on the way: `Checkbox`'s label column shrank to fit, so a label
containing a row (icon · name · right-aligned shortcut) had nothing for `ml-auto` to push
against — it grows now, which is right for every consumer. And `RailCustomiseDialog` went
into `mountedOverlays.test.ts` alongside `SettingsDialog`: both are openable from two
places and rendered from one, which is the exact shape of the five features silently lost
earlier in this rebuild.
