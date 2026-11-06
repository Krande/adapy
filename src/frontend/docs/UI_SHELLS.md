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
build time, and is selected either at build time or per deployment at runtime —
with a guaranteed way back to the classic UI at runtime.

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
  src/index.html         loads /config.js (blocking classic script) before the SPA
  scripts/gen-plugin-registry.mjs  generator (reads ADA_PLUGINS_EXTRA + ADA_UI_DEFAULT)
  packages/plugins/ui-alt/         reference shell plugin (template; not enabled)
```

`src/index.tsx` renders `<UiShellHost/>`, not `<App/>`.

## Which UI mounts — precedence

1. **`?ui=<id>`** — per-tab override. `?ui=core` is the guaranteed escape hatch
   back to the built-in UI, whatever the image was built or configured with.
2. **`localStorage["ada:ui"]`** — the user's sticky choice from the switcher.
3. **`window.ADA_UI_DEFAULT`** — the **runtime** default, served per deployment
   from `/config.js` (`ADA_VIEWER_UI_DEFAULT` on the API pod).
4. **`DEFAULT_UI_SHELL`** — the **build-time** default, stamped into the bundle
   from `ADA_UI_DEFAULT` (build-arg `UI_DEFAULT`) or `plugins.json` `defaultUi`.
5. **`core`** — the built-in UI.

**Runtime beats build time, and both lose to the user.** The runtime value is
what makes one image serve two deployments with different UIs, and makes changing
(or rolling back) the default a config edit and a pod restart instead of an image
rebuild. It sits *below* `?ui=` and the switcher so a deployment can never take
`?ui=core` away from a user.

Unset means *unset*, not `core`: an image whose deployment configures nothing
keeps whatever `UI_DEFAULT` it was built with, so this is purely additive.

An id that is not registered in this build never blanks the viewer: it is logged
and resolution continues down the list. That matters most for the runtime value,
which is the one place a typo now reaches a live deployment rather than failing a
build — the worst case is the next source down, ultimately the built-in UI.

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

### Composed from core's layout (recommended)

A shell without `load` is composed by core. Core mounts its own root (the
canvas-less routes, sign-in, the viewer provider, deep links) and its layout of
floating, dockable panels. The shell gives only data and chrome:

```tsx
import { CORE_PANEL_IDS, registerPlugin } from "@/viewer-core";

registerPlugin({
  id: "my-ui",
  version: "1.0.0",
  coreApiRange: ">=1.13 <2.0", // composed shells need plugin API 1.13
  uiShells: [
    {
      id: "my-ui",
      label: "My UI",
      layout: {
        // The split grid a user starts from; their own arrangement replaces it.
        grid: { kind: "split", id: "s1", dir: "row", sizes: [0.25, 0.75], children: [
          { kind: "group", id: "g1", panels: [CORE_PANEL_IDS.tree], active: CORE_PANEL_IDS.tree },
          { kind: "viewport" },
        ] },
        open: [CORE_PANEL_IDS.tree], // opened when the shell loads
        docks: { left: "g1" }, // plugin panels with `dock: "left"` join group g1
      },
      chrome: { header: () => import("./MyHeader"), toolbar: false },
      themeTokens: { "--ada-panel-bg": "rgb(17 24 39 / 0.95)" },
    },
  ],
});
```

The header is an ordinary component; `PanelToggles` (`@/viewer-core/layout`)
draws core's panel toggles in it and `PluginTopBarButtons`
(`@/viewer-core/plugins`) the plugins' buttons. The shell's own panels are
registered with `registerPanel` and named in the grid like core's. Each shell
keeps its own saved arrangement (`LayoutDoc.context` is the shell id).

Feature plugins' panels take part too. A slot in the `top-panel` region, or any
slot that names a `dock`, can be docked and floated like a core panel. A slot that
names a dock is placed once, the first time the shell sees it: into the group
`layout.docks` maps that dock to, else at that edge of the view (`left`,
`bottom`, otherwise `right`). While the layout shows a plugin panel in a group or
window, its region host (the panel column, the Simulation or Admin tab strip, a
core panel's section) leaves it out; its top-bar button stays and still opens and
closes it.

`themeTokens` restyles core's chrome without touching its markup. Panels read
`--ada-panel-bg`, `--ada-panel-border`, `--ada-panel-text`,
`--ada-panel-surface` and `--ada-panel-text-muted` (also set by the user's theme
picker, which a shell's tokens override while it is active). Windows and the
split grid's groups read `--ada-window-bg`, `--ada-window-border`,
`--ada-window-header-bg` (also a group's tab strip), `--ada-window-header-text`,
`--ada-window-button-bg`, `--ada-window-button-border`, `--ada-tab-active-bg`,
`--ada-tab-active-text`, `--ada-tab-text` and `--ada-group-divider`; unset, each
falls back to the classic look (`components/ui/panelChrome.ts`).

A panel that works without the 3D scene may also open in a **browser window of
its own**: it opts in with `capabilities.popout` (a plugin slot with `popout:
true`), and its header then offers "Open in a new window". The window boots
canvas-less (core's `AppRoot` routes it) with the active shell's tokens, and the
panel leaves the window it came from. Such windows make up a dashboard; see
[Multi-window dashboards](#multi-window-dashboards).

A shell with **modes** lists them in `layout.modes`. Each mode may give its own
`grid`, `open` and `docks` (else the shell's) and keeps its own saved
arrangement. Plugins' modes (`PluginSpec.modes`) follow the shell's and start
from the shell's arrangement. A panel that names `modes` shows only in those.
Leaving a mode closes the panels it placed in its grid or windows, unless the
mode entered places or opens them too. The active mode is remembered per shell,
and core reports each change to the scene colouring (a mode with
`ownsSceneColor`). `ShellModeSwitcher` and `ShellModeToolbar` (the active plugin
mode's `toolbar`) from `@/viewer-core/layout` go in the header; `setShellMode`
and `useShellMode` switch from anywhere else.

### Everything a composed shell can configure

A docked UI with a menu bar, modes, a tool rail and a status bar is a
configuration of these; none of them needs a component of the shell's own beyond
the chrome it draws in the slots.

| Area | Option | What it does |
|---|---|---|
| Arrangement | `layout.grid`, `open`, `docks` | The starting split grid, the panels opened on load, where dock-naming plugin panels go. |
| | `layout.modes[]` | Modes, each with its own `grid` / `open` / `docks`, saved arrangement, and `viewportCover` (a panel drawn over the 3D view, a converter say), `ownsSceneColor`, `showsDeformation: false` (a shell's own mode shows the deformed FEA model unless it says false; a **plugin's** mode (`PluginModeSpec`) holds it undeformed unless it opts in with `true`), `toolbar`. `?mode=` opens one; `setShellModeBadge` marks one. |
| | `layout.defaultMode` | The mode used when there is no `?mode=` and none stored from an earlier visit (else the first by order). |
| | `layout.version`, `migrate` | Re-seed or migrate users' saved arrangements when the shell ships new ones. |
| Groups (in `grid`) | `fixed: {px, min, max}` | A dock held at a px size, kept on window resize; its divider resizes it in px. On groups and splits. |
| | `collapsed`, `arrangement` | A strip of panel icons that restores the group; tabs, stacked, or `"auto"` (stacked while there is room). |
| Chrome | `chrome.header`, `footer`, `rail` | Lazy slots above, below and left of the layout (menu bar, status bar, tool rail). |
| | `chrome.toolbar` | Core's top bar of panel toggles over the view (default on). |
| | `chrome.groups` | Group header actions `"add"` / `"collapse"` / `"pin"`, and `tabIcons`. Pinned panels stay placed across modes. |
| | `chrome.commandPalette: {keys}` | The palette over every command (`registerCommand`; core adds panels and modes). `MenuBar` draws menus of command ids. |
| | `chrome.overlays: {legend, pluginPanels}` | HUD anchors over the view for core's colour legend and `overlay`-dock plugin panels; `registerViewportOverlay` adds the shell's own. |
| | `chrome.pageHeader` | The shell's title bar over core's `/convert` and `/admin` pages. |
| Panels | `panels: {[id]: PanelOverride}` | A core (or plugin) panel's title, icon, modes, capabilities and content `options` (`SceneBodyProps`, `StorageBodyProps`, ...; or a function of the mode), without a copy. |
| | `capabilities.fill`, `pinnable`, `popout` | Edge-to-edge content; may be pinned; may open in its own window. |
| | `capabilities.mirrored` (`PanelSlot.mirrored`) | May go to a pop-out window although it reads the scene: it renders from snapshots the main window mirrors (`registerMirrorTopic`) and acts through it (`registerMirrorCommand`). Core's object info panel is one. |
| Windows | Move panel → Move to window | A pop-out-capable panel moves to another open browser window, or a new one, from any Move menu (grid groups, layout windows, column panels, pop-out groups). Nothing to configure. |
| | `REOPEN_WINDOWS_COMMAND` | "Reopen windows": the main window reopens the dashboard's windows that are closed (one per click where the browser allows only one). Put it in a `MenuDef` like any command. |
| | `panels[id].available` | `false`, or a function of the active mode: hides the panel from the lists, toggles, commands and add menus (all the time, or in some modes). It can only take a panel away; a panel unavailable in itself stays so. |
| | `panels[id].bodyPalette` | Palette variables redefined inside that one panel's body (over the shell-wide `bodyPalette`), in windows, grid groups, covers, pop-outs and framed cards. |
| | docking sections | A composed shell may name a panel the classic UI draws inside another (`core:fea-table`, inside Simulation) in its `grid`, or let the user dock it; it then leaves its host. Core's own UI keeps it inside. |
| Look | `themeTokens` | `--ada-*` tokens, or a function of the user's panel theme, applied again on every theme change. Also on core's canvas-less pages (`/convert`, `/admin`). |
| | `bodyPalette` | Tailwind palette variables (`--color-gray-800`, `--color-blue-600`, ...) redefined inside core's panel bodies only (elements marked `data-ada-panel-body`), so every core body takes the shell's colours without a copy; or a function of the user's theme. Also on the canvas-less pages. A core body drawn outside a panel (a dialog, a flyout) is wrapped in `<PanelBodyPalette>` (`@/viewer-core/layout`; `panelId` adds that panel's own palette) to take it. |
| | group header | In a group narrower than `COMPACT_GROUP_PX` (340 px) the header's actions fold into one "More actions" menu; close stays in the bar. Nothing to configure. |
| | `chrome.toastAnchor` | The corner core's progress toast sits in. |
| Keys | `registerShortcut`, `registerCommand({keys})` | Chords other than core's own (`RESERVED_SHORTCUTS`). |
| | `registerCommand({chordLabel})` | A chord shown beside the command in the palette and menus, bound to nothing: for a key core already handles (`keys` would refuse it). Ignored when `keys` is given. |
| Menus | `MenuDef.items` | Command ids, separators, actions of the menu's own, or a submenu `{label, items}` (nesting). Right arrow opens a submenu, Left closes it, Escape closes the menu; a submenu with nothing offered is left out. |

Layout state helpers on `@/viewer-core/layout`: `resetArrangement`, `saveWorkspace`,
`loadWorkspace`, `deleteWorkspace`, `listWorkspaces`.

### Multi-window dashboards

Panels can be spread over several browser windows of the same browser, on
several monitors, which then work as one dashboard. Same origin and same browser
only: the windows talk over a `BroadcastChannel`, scoped by the page's path, the
active shell and the dashboard session (below), with no server involved.

**Sessions.** Every main window has its own dashboard session, so two main
windows (two tabs, two browser windows) never see each other's pop-outs, moves
or mirrored state. A session id is 8 random URL-safe characters. The main window
keeps it in its tab's `sessionStorage`, so a reload keeps the dashboard. A
duplicated tab copies `sessionStorage`; so when a tab starts with a stored id,
it asks on that session's channel (`claimSession`, waiting `CLAIM_WAIT_MS`)
whether a live main window holds it, and takes a new id if one does. The
channel is `windowBusChannelName(path, shell, session)`, pop-out URLs carry
`?session=<id>` (`SESSION_PARAM`) next to `?window=<id>`, and the browser window
name includes it (`windowTarget`), so two sessions' `w2` are two browser windows.
A pop-out opened without a session (an old link, `?popout=` typed by hand) is
**standalone**: it gets a session of its own, no main window shares it, it works
on its own grid, its Move to window menu offers only **New window** (which opens
windows in that private session), and its title bar says the main window is
closed. Its URL is rewritten to carry the session, so a reload keeps it.

**Windows.** The main window (the one with the 3D view) has the id `main`. A
pop-out window is the viewer booted canvas-less on `?window=<id>`: a split grid
of panel groups of its own -- tabs, stacks, splits, dividers, drag to dock
within the window, collapse -- and no view. Its arrangement is a `LayoutDoc`
under the context `window/<id>`, kept in this browser per session and window id
(`ada:layout-window:<shell>:<session>:<id>`; the user's name for it under
`ada:window-label:<shell>:<session>:<id>`), so a window reopened with the same id finds it again.
`?popout=<panelId>` opens a new window
holding that panel. A window is labelled ("Main", "Window 2", ...) and the user
can rename a pop-out (its title bar, or double-click the name).

**Moving panels.** Every Move panel menu (grid groups, layout windows, column
panels and in-viewer windows in the main window; groups in a pop-out window) has
**Move to window**: the other open windows, the main one first, and **New
window**. A move is a handshake: the source takes the panel out, asks the target
to dock it, and puts it back if the target does not acknowledge within
`MOVE_ACK_MS`. A pop-out docks an arriving panel in the group last used (else
its first group, else as its first group); the main window reveals it in its
place. Only panels `canPopOut` admits may go to a pop-out window: those with
`capabilities.popout` that need no canvas, and `mirrored` ones.

**One panel, one window.** The main window hides a panel another window shows
(region hosts leave a plugin panel out, too), and `reveal(id)` of such a panel
brings that window forward rather than opening a second copy. When a pop-out
window closes, its panels come back to the main window, unless another window
shows them or they are open there already; a reload within `LEAVE_GRACE_MS`
keeps them where they are. A window that stops answering (`STALE_MS`) gives its
panels back too.

**Reopening.** The main window records which windows exist and their panels.
Browsers open several windows only from a user's click, so it offers **Reopen
windows** (`REOPEN_WINDOWS_COMMAND`, also in the Move to window menu while one is
closed) rather than reopening them on load. The record is per session
(`ada:dashboard:<shell>:<session>`). One extra key per shell
(`ada:dashboard-last:<shell>`) names the session whose dashboard was recorded
last. A new session with no windows of its own (after a browser restart, which
drops `sessionStorage`) offers **Reopen windows** for that dashboard, unless its
main window is still live; reopening *adopts* those windows: their arrangements
and names are copied into the new session's keys, then they open.
**Pruning.** `ada:dashboard-sessions:<shell>` notes when each session was last
used; when a main window starts it drops the sessions (and all their keys) beyond
the newest `SESSION_KEEP` (8) or unused for `SESSION_MAX_AGE_MS` (14 days), never
its own or the last dashboard's.

**Scene panels (mirroring).** A pop-out window has stores of its own and no 3D
scene. A panel that reads the scene may still go there if it is `mirrored`: the
main window publishes serialisable snapshots of what it reads, one topic per
piece of state, on every change and on request (`registerMirrorTopic`: a
`snapshot`, a `subscribe` in the main window, an `apply` in the pop-out), and
the panel's actions go back as commands the main window runs on its own state
(`registerMirrorCommand`, `sendMirrorCommand`), which it then republishes.
Three.js objects never cross. Core mirrors the selection (`core:selection`: the
object info store) and whether an FEA session is active, and has a
`core:select-node` command; the object info panel is `mirrored`. While no main
window is open, a mirrored panel shows a "Main window closed" state.

What stays in the main window, and why:

| Panel | Why not mirrored (yet) |
|---|---|
| Object info | Mirrored for reading. Its Hide / Hide others / Unhide all and navigation buttons act on the pop-out's own (empty) scene state and do nothing there; they need commands to become remote actions. |
| Selection tree | The stock tree reads visibility from the 3D batches and its rows act on the scene through `NodeApi`s; mirroring needs a tree of its own fed by a tree snapshot, a visibility map and select / hide / isolate commands. |
| Scene info | Its ~15 sections run scene tools (section planes, face search, clash runs) against the scene directly. |
| Simulation, result data | Step and field changes run closures held in the main window's FEA store (`applyStep`, streaming reloads); they need commands per control and a snapshot of about 30 store fields plus the manifest. |

The transport is an interface (`WindowBusTransport`: `post`, `subscribe`,
`close`), so a relay over a websocket could join windows across browsers later
without the panels changing.

| Part | API |
|---|---|
| The bus (root) | `createWindowBus`, `WindowBus`, `WindowBusMessage`, `broadcastTransport`, `memoryTransportHub`, `nullTransport`, `windowBusChannelName`, `MAIN_WINDOW_ID`, `sendPanel`, `acceptPanels`, `panelsToReturn`, the dashboard record helpers, `nextWindowId` |
| URLs (root) | `WINDOW_PARAM`, `POPOUT_PARAM`, `popoutWindowId`, `popoutPanelId`, `isPopoutPage`, `windowUrl`, `popoutUrl`, `openWindow`, `canPopOut` |
| Mirroring (root) | `registerMirrorTopic`, `registerMirrorCommand`, `hostMirror`, `followMirror`, `sendMirrorCommand`, `capabilities.mirrored` |
| This page (`/layout`) | `windowBus`, `useOpenWindows`, `useThisWindow`, `useMainWindowLive`, `moveToWindowItem`, `openNewWindow`, `reopenWindows`, `installMainWindow`, `GridOps` / `GridOpsProvider`, `PopoutPage` |

### A root of its own

A shell that keeps `load` can still take core's root around its UI:
`AppRoot` from `@/viewer-core/app` gives it the canvas-less routes, sign-in, the
viewer provider and deep links, loaded lazily.

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

## Styling a shell

Every shell is styled by core's single Tailwind build. Three things make that work
across the plugin overlay, and all three are asserted by
`src/__tests__/plugins/pluginStyles.test.ts`:

| where | what | why |
|---|---|---|
| `src/index.tsx` | imports `./app.css` | the entry is the only module every shell shares. While `app.tsx` owned the import, an image defaulting to an overlaid UI loaded **no** stylesheet — no preflight, no utilities, no `html/body/#root` sizing — because `app.tsx` is itself the built-in shell, and shells are lazy-loaded. |
| `tailwind.config.js` | `content` covers `packages/plugins/**` | the JIT only emits classes it can see; a class name appearing only in a plugin package was silently dropped. |
| `src/plugins/registry.generated.css` | one `@import` per enabled package that ships `src/styles.css`, imported by `app.css` | a plugin's `@theme` registration and design tokens only take effect inside the build that has `@import 'tailwindcss'` above them. |

So a shell that wants its own tokens ships `src/styles.css` and nothing else — no
config, no build step, and no `@import 'tailwindcss'` (that would emit a second
preflight). `packages/plugins/ui-alt/src/styles.css` is the example.

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

`EXTRA_PLUGINS_ENABLE` is what decides which shells the image **carries** and is
necessarily a build input. `UI_DEFAULT` only decides which of them it **boots
into**, and that part does not have to be baked in — see below.

In forgejo CI both are Action **variables** (`EXTRA_PLUGINS_*`, `UI_DEFAULT`); the
UI repo is cloned into `src/frontend/packages/plugins/` by the workflow, so adapy's
committed source never names it. Because they are build inputs rather than repo
source, flipping `UI_DEFAULT` alone does not trigger a full viewer build — force
one (or delete the branch image) when changing it.

Omit `UI_DEFAULT` to ship **both** UIs with the classic one as the default; the
switcher appears in the menu bar as soon as a second shell is registered.

## Choosing the default at runtime instead

For a hosted deployment, set `ADA_VIEWER_UI_DEFAULT` on the API pod:

```bash
ADA_VIEWER_UI_DEFAULT=my-ui   # a shell id the image carries
```

The server puts it on `window.ADA_UI_DEFAULT` in `/config.js`, which `index.html`
loads as a **blocking, non-deferred classic script** while Vite emits the SPA
entry as a (deferred) module — so the global is always set before any shell
resolution runs. There is no async ordering to think about.

Via the chart that is `ui.default`:

```yaml
ui:
  default: my-ui
```

Leave it empty and nothing changes: the image's build-time default still applies.

Prefer this over `UI_DEFAULT` whenever the image already carries the shell.
One image then serves every deployment, deployments can disagree, and rolling a
UI back is an edit plus a restart rather than a rebuild — which also sidesteps
the "flipping `UI_DEFAULT` alone does not trigger a rebuild" trap above. Keep
`UI_DEFAULT` for images that are meant to be one-UI artefacts.

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
