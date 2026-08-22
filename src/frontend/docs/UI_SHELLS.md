# UI shells — shipping an alternative viewer UI as a plugin

## What this is

The adapy plugin system has two axes:

| Axis | Registered as | Scope |
|---|---|---|
| **Feature slots** — `panels`, `topBarButtons`, `sceneColorFields`, `resultSidecarLoaders`, `urlParamHandlers` | `registerPlugin({...})` → `src/plugins/registry.ts` | Adds things **inside** the existing UI |
| **UI shells** — `uiShells` | the same `registerPlugin({...})` → `src/plugins/uiShells.ts` | **Replaces** the whole UI |

A **UI shell** is a complete root UI. Core mounts exactly one. The stock adapy UI
is registered as the built-in shell `core` and goes through the same registry as
any other, so it is not a special case.

This exists because a UI *rewrite* (a new shell, docks, design system, a
different information architecture) is:

* too large to bolt on through panel slots;
* too churn-heavy to develop inside this repo;
* and something you want to be able to **turn off**, not fork.

So the alternative UI lives in its own repository, is overlaid into the image at
build time, and is selected by build-time registration — with a guaranteed way
back to the classic UI at runtime.

## The contract: `@/viewer-core`

A shell imports core through **one named surface** and nothing else. Without it,
an out-of-tree UI repo would import whatever core file it happened to need, and
every internal rename would silently become a breaking change for a repo we
cannot grep.

The facade is a **curated re-export barrel, not a wrapper**: core's own modules
are the implementation, and `src/viewer-core/` is the promise about which of them
are public. Nothing is adapted or re-implemented — wrapping the viewer would mean
refactoring exactly the files a UI rewrite is already churning.

Four entry points, split by **dependency weight**:

| Entry point | Contents | Why separate |
|---|---|---|
| `@/viewer-core` | contract version, `registerPlugin` + every slot type, UI-shell registry APIs | **Dependency-free** — no DOM, no store, no three. A plugin that only declares itself stays unit-testable under `node --test`. |
| `@/viewer-core/app` | `AdaViewerProvider`, stores/refs, `viewerApi`, `runtime`, scope, theme, `requestRender`, file classification, `ErrorBoundary`, `UiShellSwitcher` | Touches the DOM / creates stores at import. |
| `@/viewer-core/scene` | `CanvasWrapper`, `useUrlParamLoad`, load/unload handlers, FEA mesh + selection, camera / visibility ops, `ResizableTreeView`, `ColorLegend` | Pulls the 3D + FEA-streaming code; a canvas-less shell profile (`/convert`, `/admin`) must be able to skip it. |
| `@/viewer-core/plugins` | `PluginPanelRegion`, `PluginTopBarButtons`, `PluginColorFields`, `makePluginContext`, slot getters, sidecar loaders | Only a shell needs these — they let *other* plugins' panels appear in its chrome. The plugin context pulls the FEA module. |

`VIEWER_CORE_API_VERSION` is bumped on a breaking change to anything exported
from those four, the same way `PLUGIN_API_VERSION` covers the slot interfaces. A
shell declares what it was built against via its plugin's `coreApiRange`.

Two rules keep the facade honest, both enforced by
`src/__tests__/plugins/viewerCoreFacade.test.ts`:

* **Re-export leaf modules only** — never `@/plugins`, whose loader barrel imports
  the plugin packages, which import the facade. That cycle would bite at
  module-init time, in the browser, at boot.
* **Adding an export is a contract decision**, not a convenience. Everything there
  is something an out-of-tree repo may pin to.

The fence itself — *plugin packages may import `@/viewer-core*` and nothing else
from core* — is enforced by the same test, with an (empty) allowlist at
`src/__tests__/plugins/viewerCoreImports.allowlist.json` for a reviewed exception.
Core's own code does **not** import through the facade: core is the
implementation, and the facade exists for consumers outside it.

## The moving parts

```
src/frontend/
  src/viewer-core/       the contract: index.ts (dep-free) + app.ts + scene.ts + plugins.ts
  src/plugins/
    uiShells.ts          registry + resolution (id -> shell, precedence, fallback)
    coreUiShell.ts       registers the built-in `core` shell (lazy-loads @/app)
    UiShellHost.tsx      mounts the active shell; falls back to core on load/render failure
    UiShellSwitcher.tsx  the switcher; renders NOTHING when only one shell is registered
    registry.generated.ts  AUTO-GENERATED: enabled plugins + DEFAULT_UI_SHELL
  plugins.json           committed config: `enabled`, `defaultUi`
  scripts/gen-plugin-registry.mjs  generator (reads ADA_PLUGINS_EXTRA + ADA_UI_DEFAULT)
  packages/plugins/ui-alt/         reference shell plugin (template; not enabled)
```

`src/index.tsx` renders `<UiShellHost/>`, not `<App/>`.

## Which UI mounts — precedence

1. **`?ui=<id>`** — per-tab override. `?ui=core` is the guaranteed escape hatch
   back to the built-in UI, whatever the image was built with.
2. **`localStorage["ada:ui"]`** — the user's sticky choice from the switcher.
3. **`DEFAULT_UI_SHELL`** — stamped into the bundle at build time from
   `ADA_UI_DEFAULT` (build-arg `UI_DEFAULT`) or `plugins.json` `defaultUi`.
4. **`core`** — the built-in UI.

An id that is not registered in this build never blanks the viewer: it is logged
and resolution continues down the list.

Switching from the switcher persists the choice and reloads — a shell owns the
whole tree (canvas, websocket, stores), so hot-swapping would mean tearing all of
that down for no benefit.

## Failure isolation

`UiShellHost` falls back to the `core` shell when the active shell's chunk fails
to load (stale CDN, half-published build) **or** when it throws while rendering.
Both are logged with the `?ui=core` hint. If `core` itself fails, the error
reaches the fullscreen boundary in `index.tsx`. Net effect: an image that
defaults to a third-party UI cannot be bricked by that UI.

## Writing a shell

```tsx
// my-adapy-ui/src/register.tsx
import { registerPlugin } from "@/plugins/registry";

export function register(): void {
  registerPlugin({
    id: "my-ui",
    version: "1.0.0",
    coreApiRange: ">=1.0 <2.0",
    uiShells: [
      {
        id: "my-ui",
        label: "My UI",
        description: "…",
        order: 10,
        load: () => import("./MyShell"),   // lazy: only the active shell downloads
      },
    ],
  });
}
export default register;
```

`MyShell`'s default export is the root component. It owns providers, routing and
layout, and reuses core **through the facade**:

```tsx
import { AdaViewerProvider, UiShellSwitcher } from "@/viewer-core/app";
import { PluginPanelRegion, PluginTopBarButtons } from "@/viewer-core/plugins";
import { CanvasWrapper, ResizableTreeView, useUrlParamLoad } from "@/viewer-core/scene";
```

Re-implementing the scene would defeat the point — the shell is the chrome, not a
second viewer. Mounting the plugin slot hosts matters too: skip them and every
feature plugin's buttons and panels vanish in that UI.

Need something core has but the facade does not export? Add it to
`src/viewer-core/` in a PR (that is the contract changing, deliberately) rather
than deep-importing around the fence.

See `packages/plugins/ui-alt/` for a working minimal example, and its README for
the out-of-repo repository layout.

## Shipping it

```bash
docker build -f deploy/Dockerfile.viewer \
  --build-arg EXTRA_PLUGINS_ENABLE=my-ui \
  --build-arg UI_DEFAULT=my-ui \
  .
```

* `EXTRA_PLUGINS_ENABLE` → `ADA_PLUGINS_EXTRA` → `gen:plugins` imports the
  package (bare names are expanded to `@adapy-plugins/<name>`).
* `UI_DEFAULT` → `ADA_UI_DEFAULT` → `DEFAULT_UI_SHELL` in the generated registry.

In forgejo CI both are Action **variables** (`EXTRA_PLUGINS_*`, `UI_DEFAULT`); the
UI repo is cloned into `src/frontend/packages/plugins/` by the workflow, so adapy's
committed source never names it. Because they are build inputs rather than repo
source, flipping `UI_DEFAULT` alone does not trigger a full viewer build — force
one (or delete the branch image) when changing it.

Omit `UI_DEFAULT` to ship **both** UIs with the classic one as the default; the
switcher appears in the menu bar as soon as a second shell is registered.

## Trying it locally

```bash
cd src/frontend
ADA_PLUGINS_EXTRA=ui-alt ADA_UI_DEFAULT=alt npm run gen:plugins
npm run dev            # ?ui=alt / ?ui=core
npm run gen:plugins    # restore the committed, plugin-free registry
```

## Scope note

Shells apply to everything that boots through `src/index.tsx`: the hosted SPA
(`build:serve`) **and** the single-file desktop/Jupyter bundle
(`npm run build` → `resources/index.zip`). Note the latter inlines every dynamic
import, so a build carrying two shells carries both in one chunk — lazy `load()`
saves a request there, not bytes.

The paradoc **embed library** (`build:embed`, `src/frontend/embed/`,
`mountViewer`) is the exception: it has its own entry and its own slim UI
(`EmbedUI.tsx`) and does not go through the shell registry at all.
