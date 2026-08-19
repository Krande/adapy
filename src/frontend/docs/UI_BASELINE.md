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
