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

### Known defect, deferred to M1

**The embed has no style isolation against its host page.** `@scope (.ada-viewer-scope)`
stops embed CSS leaking *out*, but nothing stops host CSS cascading *in* — in
`embed/dev.html` the host's `button { background:#c00; border-radius:999px }` deforms
the embed's toolbar buttons. Any paradoc host page with global control styling hits
this. The fix falls out of M1 for free: once every control is a `Button`/`IconButton`
primitive setting background, radius and padding from explicit tokens, host styles
have nothing to win. Re-verify in `embed/dev.html` at the end of M1.
