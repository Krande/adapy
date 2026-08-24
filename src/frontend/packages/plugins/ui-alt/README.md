# `ui-alt` — reference UI-shell plugin

A **UI shell** is a plugin-contributed *whole* viewer UI. Core mounts exactly one
shell; the stock adapy UI is registered as the built-in shell `core`, and an
alternative UI is just another entry in the same registry
(`src/frontend/src/plugins/uiShells.ts`).

This package is the template. It is **not enabled** in a stock build — it exists
so an out-of-tree UI repo has a working shape to copy, and so the mechanism is
exercised in CI.

## Why a separate repo

A UI rewrite is too large and too churn-heavy to develop inside adapy, and it
must be swappable rather than a fork. So:

* the alternative UI lives in **its own repository**, with its own release cycle;
* CI **clones it into `src/frontend/packages/plugins/<name>/`** during the image
  build (the same overlay path the external feature plugins already use);
* the build **registers it and picks the default UI**;
* adapy's committed source never names it.

## Building an image whose default UI is the alternative one

```bash
docker build -f deploy/Dockerfile.viewer \
  --build-arg EXTRA_PLUGINS_ENABLE=my-ui \
  --build-arg UI_DEFAULT=my-ui \
  .
```

`EXTRA_PLUGINS_ENABLE` (→ `ADA_PLUGINS_EXTRA`) makes `npm run gen:plugins` import
the package; `UI_DEFAULT` (→ `ADA_UI_DEFAULT`) stamps `DEFAULT_UI_SHELL` into the
generated registry. Omit `UI_DEFAULT` to ship both UIs with the classic one as
the default and the alternative reachable from the switcher / `?ui=my-ui`.

## ...or picking the default per deployment

Which shells an image **carries** is a build decision. Which one it **boots
into** does not have to be: set `ADA_VIEWER_UI_DEFAULT=my-ui` on the API pod
(chart value `ui.default`) and the server hands it to the browser through
`/config.js`. It outranks the build-time default and is outranked by the
switcher and `?ui=`, so one image can serve several deployments and a UI
rollback is a config edit plus a restart. Unset changes nothing.

## Trying this scaffold locally

```bash
cd src/frontend
ADA_PLUGINS_EXTRA=ui-alt ADA_UI_DEFAULT=alt npm run gen:plugins
npm run dev
# ?ui=core  -> the classic UI, always
# ?ui=alt   -> this scaffold
```

`npm run gen:plugins` with no env restores the committed (plugin-free) registry.

## Styling a shell

A shell is styled by core's Tailwind build, not by one of its own:

* `src/app.css` — the Tailwind entry — is imported by `src/index.tsx`, the entry
  every shell boots through. (It used to be imported by `app.tsx`, which is itself
  the built-in shell and therefore lazy-loaded, so an image defaulting to another
  UI loaded no CSS at all.)
* `tailwind.config.js` scans `packages/plugins/**`, so class names that appear
  only in a plugin package are generated.
* A package may ship `src/styles.css`. `gen:plugins` emits an `@import` for it
  into `src/plugins/registry.generated.css`, which `app.css` imports — so a
  shell's own design tokens and `@theme` registrations join the same Tailwind
  build. See this package's `src/styles.css`.

Do **not** put `@import 'tailwindcss'` in a plugin stylesheet: it would emit a
second copy of preflight and of every utility.

## What a shell may and may not do

* **May** replace everything above the scene: layout, routing, panels, theming.
* **Must** import core through `@/viewer-core` (+ `/app`, `/scene`, `/plugins`)
  and nothing else. That facade is the contract; deep imports into core internals
  fail `src/__tests__/plugins/viewerCoreFacade.test.ts`. Missing something? Add
  it to the facade in a PR — that is the contract changing, deliberately.
* **Should** reuse core's stores, services and `CanvasWrapper` rather than
  re-implementing the scene — that is the whole reason this is a shell and not a
  fork — and should mount the plugin slot hosts, or feature plugins go invisible
  in that UI.
* **Must not** assume it is the only shell: `?ui=core` has to keep working, which
  it does as long as the shell does not persist global state the classic UI then
  chokes on.

The paradoc embed library (`mountViewer`, `src/frontend/embed/`) has its own
entry and its own slim UI, and does **not** go through the shell registry. The
single-file desktop/Jupyter bundle (`resources/index.zip`) does.
