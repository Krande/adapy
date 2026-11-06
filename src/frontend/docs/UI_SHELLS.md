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
    coreUiShell.ts       registers the built-in `core` shell (lazy-loads @/app: the classic preset, through the layout engine)
    UiShellHost.tsx      mounts the active shell; falls back to core on load/render failure
    UiShellSwitcher.tsx  the switcher (Options -> Theme); renders NOTHING when only one shell is registered
  src/layout/desktop/    the built-in Desktop UI (a composed shell, registered next to `core`)
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

Inside the built-in UI there is no second switch: it is always the classic
preset of the layout engine. `?layout=classic` (and the
`ada:layout-engine` key), which once fell back to a hand-written tree, are
ignored.

Switching (the Options switcher, View → User interface, `setActiveUiShell`)
persists the choice and then either happens in place or reloads the page.

### What a switch keeps

**In place: between core's own UI and composed shells.** Classic ↔ Desktop, and
to or from any plugin shell without `load`. `UiShellHost` keeps one root of core's
mounted (`CoreShellRoot`: the viewer provider, the sign-in gate, the 3D canvas,
the layout) and only tells it which shell to show; the elements around the layout
keep their places, so React remounts nothing below it. Kept: the websocket, the
loaded models (from storage, a deep link, File → Open or a drop, the websocket),
the camera, the selection, hidden objects, the FEA result on screen, open
panels (shown at their place in the shell switched to), the undo history, every
store. Swapped: the shell's header, footer and rail, its modes, its arrangements
(each shell keeps its own saved documents), the top bar, panel overrides, theme
tokens and body palette. The command palette is core's and stays (its extra chord
follows the shell). The shell left behind is cleaned up: a mode that owned the
scene colouring hands it back, and for a
plugin's shell everything its plugin registered while the shell was up (commands,
shortcuts, HUDs over the view: its chrome's) is taken down and given back when
the shell returns. What the plugin registered before its shell came up (its
feature plugin's) stays. `?ui=` is dropped from the address bar, so a manual
reload opens the shell chosen. A multi-window dashboard stays the shell the page
booted in (`bootUiShellId`): its pop-out windows keep talking to this one, and a
new one opens in that shell; a reload rebinds it.

**By a reload: a shell with `load`** (it brings a root of its own) **or one that
sets `reloadOnSwitch`** (a composed shell that sets up something at load time it
cannot take down), in either direction, and every switch after a shell crashed
into the core fallback. The reload carries what it can:

- *A deep-linked model.* `useUrlParamLoad` strips `?scope&file&derived` (and
  every other param that came with them) once it has started the load, so the
  address bar no longer says what is on screen; it registers the stripped params
  with `carryOnShellSwitch`, and the reload goes to `shellSwitchUrl(...)`, which
  puts them back. They are carried while the load is running and, after it, only
  while the file is still loaded (a model the user closed does not come back);
  `ui` is never carried, a param already in the URL wins, a throwing carry is
  skipped. Nothing is written to the address bar for good, so a bookmark or a
  manual reload behaves as before, and a deep-linked load is still not added to
  the scope's scene record.
- *Models from storage or the asset browser* (REST): the scene restore brings
  them back from the scope's scene record, with the camera, hidden objects and
  selection, as after any reload. The switch marks its reload
  (`loadedByShellSwitch`), so they come back even with "restore after reload" off.
- *Not carried:* a file opened from disk (File → Open, a drop) has no server copy,
  and a websocket viewer's scene is the server's; they survive in-place switches
  only. Stashing local files' bytes for a reload was left out: a model can be
  hundreds of megabytes, and the switches users make (Classic ↔ Desktop) are in place.

**Modes** of a composed shell (`setShellMode`) never reload or remount: the
canvas, the models and the selection stay; panels the mode left placed go with it
(unless pinned), and a mode that owns the scene colouring suspends and restores it.

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
| | `layout.customisablePanels` | `"required"` (default): floating, docking and the split grid are always on in the shell, whatever the user set in another UI, and Options → Theme → Customisable panels shows the toggle on and disabled ("Always on in the … UI"); a docked layout is built on them. `"optional"`: the user's own setting for this shell. The setting is kept per shell (`LayoutPrefs.customisablePanelsByShell`; core's own UI's under `core`, where the single setting of older versions moves), so turning it off in Classic leaves the Desktop UI as designed and comes back on the way back. Read it through `customisablePanels()` / `useCustomisablePanels()`. |
| Groups (in `grid`) | `fixed: {px, min, max}` | A dock held at a px size, kept on window resize; its divider resizes it in px. On groups and splits. |
| | `collapsed`, `arrangement` | A strip of panel icons that restores the group; tabs, stacked, or `"auto"` (stacked while there is room). |
| Chrome | `chrome.header`, `footer`, `rail` | Lazy slots above, below and left of the layout (menu bar, status bar, tool rail). `rail` may instead be core's tool rail as data, `{tools}` (see [The tool rail](#the-tool-rail)). |
| | `chrome.markingMenu: {items, more, gesture} \| false` | Core's marking menu over the 3D view (see [The marking menu](#the-marking-menu)): given, on in the shell; absent or `false`, none. |
| | `chrome.toolbar` | Core's top bar of panel toggles over the view (default on). |
| | `chrome.groups` | Group header actions `"add"` / `"collapse"` / `"pin"`, and `tabIcons`. Pinned panels stay placed across modes. |
| | `chrome.commandPalette: {keys} \| false` | The palette over every command (`registerCommand`; core adds its own, panels and modes) is core's, in every UI (core's own UI, composed shells, shells with `load` under `AppRoot`), mounted once under core's root and opened by core's Ctrl+K (`core:command-palette`, reserved, not while typing in a field; the browser's own Ctrl+K is taken over) or `openCommandPalette()`. `keys` adds a chord of the shell's own beside Ctrl+K; `false` turns the palette off while the shell is active. Absent: on. `MenuBar` draws menus of command ids. |
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
| Keys | `registerShortcut`, `registerCommand({keys})` | Chords other than core's own (`RESERVED_SHORTCUTS`, derived from the registry, see below). A refused chord is warned about and ignored. |
| | `registerCommand({shortcut})` | The id of a core shortcut (`core:fit-all`, ...): the command shows that entry's chord in the palette and menus. Binds nothing itself; ignored when `keys` is given. Core's own commands name their keys this way. |
| | `registerCommand({chordLabel})` | A display-only chord for a key handled outside the registry. Prefer `shortcut`. |

Core's own keys live in the same registry as yours. `CORE_SHORTCUTS` (ids under `core:`) lists every
one; `listShortcuts()` returns them first, then registered ones and non-core panels' toggles, and the
shortcuts dialog draws exactly that list, grouped (`CORE_SHORTCUT_GROUPS`). `RESERVED_SHORTCUTS` is the
chords of the entries plugins may not take. One keydown listener (`installShortcutDispatcher`, shared by
the layout and a mounted viewer, so a key never fires twice) runs the dispatched entries; it ignores keys
typed into an input, textarea, select or editable element and keys a widget already handled.

Two kinds of core entry. *Dispatched* ones (Shift+H / I / G / U / F / A / Q / W / P / R / T / C / E and
Shift+Arrows, Ctrl+Z / Y / Shift+Z / C, Shift+Enter in the procedural builder) run in the registry's
listener, bound by the mounted viewer. *Local* ones (`local: true`) are keys a widget keeps because it needs
its own state: the gallery strip's arrows and PageUp / PageDown, the procedural builder's tool keys and
numeric entry, a tree's Esc, the panel-zone keys F6 / Shift+F6 and Esc on a floating window. They are
listed in the dialog and (F6, Esc) reserved, but never dispatched. Rebinding a core key is not offered.
| Menus | `MenuDef.items` | Command ids, separators, actions of the menu's own, or a submenu `{label, items}` (nesting). Right arrow opens a submenu, Left closes it, Escape closes the menu; a submenu with nothing offered is left out. |

Layout state helpers on `@/viewer-core/layout`: `resetArrangement`, `saveWorkspace`,
`loadWorkspace`, `deleteWorkspace`, `listWorkspaces`.

### The marking menu

A wheel of up to eight commands at the pointer over the 3D view, with a short list under it
for what does not fit. Core's, in the main window of every UI that has it on; a pop-out window
has no 3D view and no marking menu.

**The gesture, and why that one.** Over the view the right button already does two things: a
quick click opens the Scene menu for what is under the cursor, and a drag pans the camera
(Shift + left drag is the selection box, left drag orbits, a left click picks). The marking
menu takes the one thing left, a **hold**: press the right button and keep it still for
`holdMs` (300 ms) and the menu opens at the pointer. Then:

- *flick and release* -- move past `deadZonePx` (24 px) towards an item and let go: it runs.
  Past a submenu's label the submenu's wheel opens there and the flick goes on from it.
- *release without moving* -- the menu stays open: hover highlights, a click on an item or
  anywhere in its sector runs it, a click in the centre or far off closes it.
- *keys* -- core's `core:marking-menu` (**Shift+E**, reserved) opens it at the pointer when
  that is over the view, else at the view's centre, with the first item that can run
  highlighted; arrows (and Tab) move round the wheel and through the list, Enter runs,
  Backspace leaves a submenu, Escape closes. The highlighted item has the focus, and the focus
  goes back where it was when the menu closes.

A press that moves more than `moveTolerancePx` (5 px) before the hold is a pan, as before; a
quick click is the Scene menu, as before. Browsers on macOS and Linux fire `contextmenu` on the
*press*: core holds that event back until it knows which of the three the press is, and hands
it on to the Scene menu for a click (and drops it for a pan or a hold). Once the menu opened,
the camera's drag is cancelled and the moves of the flick are kept from it, and the
`contextmenu` Windows fires on the release is swallowed. Shift / Ctrl / Alt + right press, touch
and pen input, a press on a HUD or a panel over the view, and the procedural builder (it has
right-click menus of its own) are left alone. The rules are a state machine with no DOM
(`MarkingGesture`), tested on its own.

**Items.** Command ids (`registerCommand`; core's, `panel:<id>`, `mode:<id>` too), clockwise
from north; `null` keeps a slot empty and a `{label, items}` is a submenu. An item keeps its
place: a command that cannot run is greyed where it is, its reason in the tooltip, and a command
nothing offers is an empty slot. Core's wheel (`defaultMarkingMenuItems`): Fit all (N), Fit
selection (NE), Select all (E), Clear selection (SE), Views ▸ (S: top, isometric, right, front,
left, back), Hide others (SW), Hide selected (W), Show all (NW); its list
(`defaultMarkingMenuMore`): the picked element's FEA sets and Clear FEA isolation while a
result is loaded, Undo, Redo.

```ts
chrome: {
  markingMenu: {
    // A list, or a function asked each time the menu opens.
    items: (ctx) => [CORE_COMMAND_IDS.fitAll, ..., ctx.mode === "results" ? "my-shell:probe" : null],
    more: [CORE_COMMAND_IDS.undo, CORE_COMMAND_IDS.redo],
    gesture: { holdMs: 250 },
  },
},
```

| Where | On by default | Why |
|---|---|---|
| Core's own UI | No; Options -> Theme -> Marking menu turns it on | Its right button has meant "Scene menu or pan" for a long time; a hold opening something new should be a choice. |
| A composed shell with `chrome.markingMenu` | Yes; the user may turn it off | The shell asked for it. |
| A composed shell without it, or `false` | Not offered | It may have right-button gestures of its own. |
| A shell with `load` | Not offered | It mounts no core layout. |

The user's choice is per UI shell (`LayoutPrefs.markingMenuByShell`), like "Customisable
panels". Core's commands: `markingMenu` (open it; offered where it is on) and
`toggleMarkingMenu` (on or off, checked while on). On `@/viewer-core/layout`:
`MarkingMenuView` (the wheel as drawn, for a shell that opens it its own way), `markingSlots`,
`MarkingMenuHost`, `openMarkingMenu`, `setMarkingMenuOn`, `servedMarkingMenu`; the rules on the
root: `MarkingGesture`, `sectorAt`, `sectorOffset`, `resolveMarkingMenu`,
`markingMenuAvailability`.

### The tool rail

`chrome.rail` takes core's rail as data, in place of a component of the shell's own (which it
still takes):

```ts
chrome: { rail: { tools: [CORE_COMMAND_IDS.fitAll, CORE_COMMAND_IDS.fitSelection, RAIL_SEPARATOR, "panel:core:tree"] } },
```

A column of icon buttons down the left edge of the layout, outside the docks, between header and
footer. Each tool is a command: its `icon` (`CommandSpec.icon`; core's commands have icons,
`CORE_COMMAND_ICONS`, panels and modes use theirs, and a command without one is drawn with its
initials), its label and chord as the tooltip, `aria-pressed` while a toggle is `checked`,
greyed with the reason while it cannot run. A tool nothing offers now is left out, and so are
the separators that leaves stranded. What greys or ticks a command lives in stores of every
kind, so the rail asks its tools again after a click or a key anywhere, when the selection, the
history or the layout changes, and once a second.

The user arranges it (`customisable: false` takes that away): right-click a tool to remove it;
the button at the foot of the rail, its right-click menu and `core:customise-rail` (Window ->
Customise tool rail… in the Desktop UI) open the customise dialog, which reorders, removes and
adds tools from every command (with a filter), adds separators and resets to the shell's list.
Changes are saved as they are made, per shell, in the layout prefs (`LayoutPrefs.rail[shell]`:
`RailPrefs`), so they go to the server with the arrangements. The saved value is the user's
whole list and the shell's list when they saved it (`base`): a tool the shell adds in a later
release is in neither and is put in next to the tool before it in the shell's list
(`railTools`); one the user removed is in `base` and stays out. A list equal to the shell's is
not stored. On `@/viewer-core/layout`: `ToolRail`, `ToolRailCustomiseDialog`,
`openRailCustomise`, `saveRail`, `resetRail`, `railList`; the rules on the root:
`railTools`, `tidyRail`, `moveRailTool`, `addRailTool`, `removeRailTool`, `migrateRailPrefs`.
Main window only: a pop-out window draws no shell chrome.

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
object info store, plus the selected range count and the parent level its up
arrow selects) and whether an FEA session is active; the object info panel is
`mirrored`. While no main window is open, a mirrored panel shows a "Main window
closed" state.

**Acting from a pop-out (`useSceneActions`).** A scene panel acts through one
seam, `useSceneActions()` (`@/viewer-core/layout`), so its code is one path in
every window: in the main window each action runs on its scene; in a pop-out
(`isMirroredWindow()`) it is sent to the main window as a mirror command. Actions
that are core commands -- hide selected, hide others, show all, fit all, fit
selection, clear selection, undo, redo -- go as `core:run-command` `{id}`, which
runs only the ids in `MIRRORED_CORE_COMMANDS`, through the core command's own
action. The rest have commands of their own (`SCENE_ACTION_COMMANDS`):
`core:select-node` `{id}`, `core:select-parent`, and the tree's row commands
below. Arguments are ids and plain values; the main window resolves ids to its
own tree rows and drops unknown ones. The object info panel's Hide, Hide others,
Unhide all, Fit all, Go to object and up-a-level buttons work from a pop-out
this way.

**The scene tree in a pop-out (`core:scene-tree`).** The selection tree panel is
`mirrored`. In a pop-out its content is the Scene tree alone (the Sources and
Systems tabs load into a scene the pop-out does not have), the same component
and rows as in the main window (`rowExtras`, `rowClassName`, search, the root
label toggle). The main window publishes one topic, built for large trees
(`sceneTreeTopic`, root facade, dependency-free):

* **full** -- the tree, every row's visibility and the selected rows, sent when
  a window asks and when the *tree* changes (a model loaded or unloaded, roots
  relabelled), throttled (250 ms, trailing) so a bulk load sends a few trees, not
  one per model. The tree travels flat (pre-order columns: ids, names, parent
  indices, the rows' other fields) in chunks of 5000 rows
  (`SCENE_TREE_CHUNK`), the first in the full message and the rest as **part**
  messages in tasks of their own, so the main window is not held up by one huge
  clone. The pop-out builds the tree once every part is in and puts it into its
  own tree store, so the stock tree renders it.
* **delta** -- otherwise only what changed: the rows whose visibility changed (a
  code each: shown, hidden, partial, no geometry) and the selected rows when they
  changed. Hiding one element of a 20 000-row tree sends a few codes.

Every tree has a revision; a part or delta for a revision a pop-out does not
hold is refused and the pop-out asks for the full state again (a topic's
`apply` returning `false`, `MirrorTopic.delta`); a full for the revision it
already holds is not rebuilt.

The rows act through `useSceneActions`: a click (with Shift / Ctrl ranges and
toggles resolved in the pop-out, whose expand state is its own) is
`core:select-rows` `{ids, mode}` (`replace`, `add`, `toggle`); the eye is
`core:hide-rows` / `core:show-rows` `{ids}`; the pop-out's row menu offers Go to
(`core:fit-rows`: select and frame), Hide, Unhide, Hide all others
(`core:isolate-rows`), Unhide all, Copy names (its own clipboard) and
expand / collapse below; "Remove all" is `core:clear-scene`. A selection made in
a pop-out is shown in the main window's tree too, and a pick in the main window
selects and scrolls to the row in the pop-out's tree. Expand state, search,
scope and scroll stay in each window.

Limits: a full tree costs O(rows) to flatten, clone and rebuild, once per
model-set change and per window that opens (every open pop-out receives it, not
only those showing the tree). A visibility change costs the main window one
O(rows) walk to find what changed; on the wire it is O(changed rows), so
isolating a small part of a large tree sends close to a code per row (still no
tree). Rows the main window has no mesh for yet show as "no geometry" until the
next visibility change, as in the main window.

What a pop-out can do of each scene panel, and what stays in the main window:

| Panel | Mirrored, and what is not (yet) |
|---|---|
| Object info (parts) | Mirrored, buttons included. Still local to the pop-out: the mobile-only "Copy all (n)" (it copies the 3D selection's names, which the pop-out does not hold) and "+ Add mode" (the main window's additive picking); the procedural-cell context (cell names, cell hide, cell add mode: the builder store is not mirrored); "Show in data" (it resolves the picked element on the main window's FEA mesh, so it does not show); the selection download (it reads the 3D selection). |
| Selection tree (parts) | Mirrored (Scene tab). Not in a pop-out: the Sources and Systems tabs; from the row menu, saved groups, related elements, loading from another provider, unloading one model, the selection box. |
| Scene info | Not mirrored. Its ~15 sections run scene tools (section planes, face search, clash runs) against the scene directly; each would need its state as a topic and its actions behind the act seam. |
| Simulation, result data | Not mirrored. The body (`SimulationBody`) reads about 35 FEA store fields at once, among them the three.js result `mesh` whose morph influence the factor / scale controls write directly, and the step and field controls run closures held in the main window's store (`applyStep`, `startFramePreload`) or reload the stream (`load_fea_streaming`, `selectFeaResultComponent`). The body also hosts the colour legend, the FEM concepts panel, the animation export and the result data table, each reading its own stores. What mirroring it needs: (1) a `core:fea-controls` topic with the plain fields the controls render -- `sourceName`, `fieldName`, `reduction`, `stepIndex`, `nSteps`, `isPlaying`, `factor`, `period`, `scaleFactor`, `colormap`, `warpEnabled`, `layer`, `ipReduction`, `nodalAverage`, `envelopeMode`, `timeHistory`, the beam-solid and colour toggles, `framePreload` progress -- plus the manifest's field and step lists (`manifest.fields` reduced to name / per_type / support / surface, the step slots of `mergeCaseSteps`, the case statuses); (2) one command per control (`core:fea-step` {index}, `core:fea-field` {name}, `core:fea-reduction`, play / pause / stop, factor, scale, warp, layer, IP reduction, nodal average, envelope, colormap, the toggles), each running that control's handler in the main window; (3) the handlers moved out of the component behind the act seam, so the body calls them in one path; (4) the legend, FEM concepts, export and data table either mirrored as panels of their own or left out of the pop-out body. |

The transport is an interface (`WindowBusTransport`: `post`, `subscribe`,
`close`), so a relay over a websocket could join windows across browsers later
without the panels changing.

| Part | API |
|---|---|
| The bus (root) | `createWindowBus`, `WindowBus`, `WindowBusMessage`, `broadcastTransport`, `memoryTransportHub`, `nullTransport`, `windowBusChannelName`, `MAIN_WINDOW_ID`, `sendPanel`, `acceptPanels`, `panelsToReturn`, the dashboard record helpers, `nextWindowId` |
| URLs (root) | `WINDOW_PARAM`, `POPOUT_PARAM`, `popoutWindowId`, `popoutPanelId`, `isPopoutPage`, `windowUrl`, `popoutUrl`, `openWindow`, `canPopOut` |
| Mirroring (root) | `registerMirrorTopic` (`MirrorTopic.delta`, `apply` returning `false`), `registerMirrorCommand`, `hostMirror`, `followMirror`, `sendMirrorCommand`, `capabilities.mirrored`; for trees: `sceneTreeTopic`, `flattenTree`, `buildTree`, `SceneTreeSource` / `SceneTreeSink`, `ROW_VISIBILITY_CODES`, `SCENE_TREE_CHUNK` |
| Scene tree mirror (`/layout`) | `SCENE_TREE_TOPIC`, `coreSceneTreeSource`, `coreSceneTreeSink`, `useSceneMirrorStore` |
| This page (`/layout`) | `windowBus`, `useOpenWindows`, `useThisWindow`, `useMainWindowLive`, `moveToWindowItem`, `openNewWindow`, `reopenWindows`, `installMainWindow`, `GridOps` / `GridOpsProvider`, `PopoutPage` |
| Acting (`/layout`) | `useSceneActions`, `sceneActions`, `remoteSceneActions`, `isMirroredWindow`, `SceneActions`, `RowSelectMode`, `SCENE_ACTION_COMMANDS`, `MIRRORED_CORE_COMMANDS` |

### The built-in Desktop UI

Core ships one composed shell of its own next to the classic UI: **Desktop**
(`DESKTOP_UI_SHELL_ID`, `"desktop"`; `src/layout/desktop/`). It is registered at
startup by `loadPlugins`, like core's panels, so it is in every build without
`ADA_PLUGINS_EXTRA`; the classic UI stays the default. Pick it in Options ->
Theme -> User interface (sticky, `localStorage["ada:ui"]`), or per tab with
`?ui=desktop`; View -> User interface switches back.

It is nothing but configuration of the options above, so it doubles as the
worked example of a full docked UI (`desktopUiShellSpec()` from `@/viewer-core`):

| Part | What it is |
|---|---|
| Header | A menu bar (`MenuBar`) in place of core's top bar (`chrome.toolbar: false`), the mode switcher and toolbar, the plugins' top-bar buttons, a Commands button. |
| Layout | Tree and storage tabbed in a fixed-px left dock; object info over scene info in a fixed-px right column; Simulation and Result data in a fixed-px dock under the view, collapsed to a strip in the Model mode. Group actions add, collapse, pin; tab icons. Plugin panels naming `left` / `right` / `bottom` join those docks. |
| Modes | Model (default) and Results (the results dock open); plugins' modes follow. |
| Footer | A status bar: connection, models loaded, selection count, mode, what Undo undoes. |
| Rail | Core's tool rail (`DESKTOP_RAIL_TOOLS`) on the left edge: Open; Fit all, Fit selection, Reset camera, Isometric view; Hide selected, Hide others, Show all; Select all, Clear selection; Geometry edges, Clear FEA isolation; Undo, Redo. The user's own is kept per shell. |
| Marking menu | Core's (`chrome.markingMenu: {}`), on: hold the right button over the view, or Shift+E. |
| Chrome | Command palette on Ctrl+K, the colour legend at `right-center`, overlay plugin panels at `top-left`, the toast bottom-right, a title bar on `/convert` and `/admin`. |
| Look | Core's tokens only (`--ada-window-*`, `--ada-tab-*`, the user's panel theme); no colours of its own. |

The menus are command ids only (`desktopMenus()` on `@/viewer-core/layout`; an
item whose command is not offered is left out, and so are the separators it
leaves stranded):

| Menu | Items |
|---|---|
| File | Open… (Ctrl+O), Upload file… (REST); Export selection…, Export model… (REST); Storage, Convert files; Options |
| Edit | Undo, Redo; Select all (Ctrl+A), Invert selection, Clear selection, Copy names, Save selection as group…; Visibility ▸ Hide selected, Hide others, Show all |
| View | Panels ▸ (each panel's toggle); Camera ▸ Fit all, Fit selection, Reset camera, Top / Bottom / Front / Back / Left / Right / Isometric view; Display ▸ Geometry edges, Performance stats, Gallery mode, Marking menu on the right button; FEA sets ▸ Isolate FEA sets…, Isolate the picked element's FEA sets, Clear FEA isolation, Show the rest as wireframe; Theme ▸ (the presets); User interface ▸ (the shells); Command palette, Marking menu (Shift+E), Full screen |
| Modes | One per mode |
| Window | New window, Reopen windows; Reset layout, Reset every mode's layout, Customise tool rail…; Workspaces ▸ Save workspace…, (each saved one), Delete workspace… |
| Help | Keyboard shortcuts; Server info, Connection; About |

**Core's commands.** The items that are not `panel:` or `mode:` commands are
core's own (`CORE_COMMAND_IDS` on `@/viewer-core`, with `themeCommandId`,
`uiShellCommandId`, `workspaceCommandId`), registered for **every** composed
shell, so another shell's menus can name them too. Each wraps an action core
already has: the viewer's keys (each names its registry entry with `shortcut`, so
the chord shown is the real one: Ctrl+Z, Shift+H, Shift+A, ...), the Options panel's toggles, the theme presets, the switcher,
`openNewWindow`, `resetArrangement` and the workspaces, and the shortcuts dialog
(`openShortcutsDialog`; a composed layout mounts its host). A command greys
itself with a reason where the key would do nothing (nothing selected, nothing
to undo, no 3D view).

Core also has commands made for the menus, each a scene action of its own:

| Command | What it does, and where it stops |
|---|---|
| `openFile` (File → Open…, Ctrl+O) | A file picker; GLB and glTF are read in the browser and loaded straight into the scene (replacing it), in any viewer, with no upload. A glTF must have its buffers and images embedded. Other formats need the server to convert them: on a REST viewer whose server converts, the user is asked, then the file is uploaded to storage and loaded through the load queue (converted on the way); elsewhere it is refused with the reason. Files dropped from the desktop on the 3D view open the same way, added to what is loaded. Greyed with "No 3D view" off the canvas. |
| `selectAll` (Ctrl+A), `invertSelection` | Select every shown element of the loaded models, or the shown ones that are not selected. Hidden elements and switched-off models are never taken. One undo entry each. "Nothing to select" when nothing is shown. |
| `viewTop` … `viewIsometric` (`cameraViewCommandId(view)`, `CAMERA_VIEWS`) | The model framed from one side, as Fit all frames it (smoothly with the default camera controls). Z up by default (front looks along +Y), the same views turned when the viewer is Y up. With nothing loaded the camera turns about its target. |
| `exportSelection`, `exportModel` | The Export dialog, on the selection or on one whole loaded model, with the format (STEP or IFC). Written by the server from the model's source, as the object info panel's "Download as…" (which the Selection scope reuses, with its export options); a model with no source behind it (a GLB, a file opened from disk) cannot be exported and the dialog says so. Greyed off a REST viewer. |
| `about` | The About dialog: the viewer's build, the viewer core and plugin API versions, the UI, the connection, what the server's `/api/config` reports (its image tags), and links to the documentation. |
| `feaIsolateSets`, `feaIsolateSelectionSets`, `feaClearIsolation`, `feaToggleContextWireframe` | The loaded FEA result's set isolation (below): isolate the element sets whose name holds a text (asked for), or the sets the picked element is in; show the whole model again; draw what the isolation hides as its element edges or not (checked while on). Greyed with the reason: no FEA result, no sets or properties, nothing isolated, no element edges in the result, an isolation that names no elements. Scene actions, so a pop-out window's palette acts on the main window's scene. |

**FEA sets and properties, in every UI.** While an FEA result is loaded core adds
rows of its own to the Scene tree, after the models (a provider of
`registerSceneTreeRoots`, `core:fea-tree`): **Sets** (element sets and node
sets, with counts; node sets are `TreeNodeData.informational`, since nodes name no
faces) and **Properties** (each `category: "property"` field of the manifest, its
values and how many elements carry each, read from the field's blobs the first
time the rows or a pick need them). Selecting element-set or value rows isolates
their union; an isolated row is `TreeNodeData.marked`. Right-clicking one does not
select it: its row menu isolates, adds to or removes from the isolation, clears
it, toggles the rest as wireframe, hides or shows its elements. Each change is one
undo entry, the isolation a slice of it. The Selected Object panel's `fea-sets`
section (in `CORE_PROPERTY_PROVIDERS`) lists a picked element's values and sets
with Isolate buttons. In a pop-out window the rows, the menu, the section and the
commands read the main window's view (`core:fea-tree`) and act through the scene
actions (`feaIsolate`, `feaClearIsolation`, `feaToggleContext`, ...). A shell
therefore needs nothing of its own for them; a plugin scopes the view with
`isolateFeaSets` / `isolateFeaRows` on `@/viewer-core/scene`, which also has the
rows' pure builders and ids (`buildFeaTreeRoots`, `elementSetRowId`, ...), the
reasons (`feaIsolationReason`, `feaContextReason`), `feaTreeView` /
`subscribeFeaTreeView` and `useFeaIsolationStore`.

The dialogs are drawn by the composed layout (`openCoreDialog` / `closeCoreDialog`
on `@/viewer-core/layout`). There are no keys for the camera views: the shortcut
registry matches `KeyboardEvent.key`, so the numeric keypad cannot be told from
the digit row.

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
switcher (Options -> Theme) lists every registered shell. Since core registers
the Desktop UI itself, it is there in every build.

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
