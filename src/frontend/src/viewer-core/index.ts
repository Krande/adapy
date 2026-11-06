// `@/viewer-core` — the stable surface a UI shell is allowed to depend on.
//
// WHY THIS EXISTS
// ---------------
// A UI shell (see `@/plugins/uiShells`) replaces the whole viewer UI. Without a
// named contract it would import whatever core file it happened to need, and
// every internal rename would become a breaking change for an out-of-tree UI
// repo we cannot grep. This facade turns that implicit, unbounded coupling into
// an explicit one: a shell imports ONLY from `@/viewer-core*`, and everything it
// depends on is listed here in one reviewable file.
//
// It is a CURATED RE-EXPORT BARREL, not a new abstraction layer. Nothing is
// wrapped, adapted or re-implemented — core's own modules are the
// implementation, and this file is the promise about which of them are public.
// That is deliberate: wrapping the viewer would mean refactoring exactly the
// files a UI rewrite is already churning, for no gain in swappability.
//
// THE ENTRY POINTS, split by dependency weight — so a canvas-less shell profile
// (a /convert or /admin page) does not drag the 3D + FEA code into its chunk,
// and a plugin that merely declares itself stays testable without a DOM:
//
//   `@/viewer-core`          this file. DEPENDENCY-FREE: the contract version,
//                            plugin declaration + every slot type, the
//                            UI-shell registry APIs, and the panel registry
//                            and panel intents. Imports no DOM, no store,
//                            no three — so `node --test` can import it, which is
//                            the same property `@/plugins/registry` is written
//                            for and the reason that rule is tested, not hoped.
//   `@/viewer-core/app`      providers, stores/refs, REST client, runtime
//                            config, scope, theme, file classification.
//   `@/viewer-core/scene`    the canvas, model loading, camera / selection /
//                            visibility ops, and the reusable scene chrome.
//   `@/viewer-core/plugins`  the slot HOSTS a shell mounts so OTHER plugins'
//                            panels appear in its chrome. Separate because the
//                            plugin context pulls in the FEA streaming module.
//   `@/viewer-core/layout`   core's panel contents (each panel without its
//                            classic frame), the shared panel chrome, and the
//                            layout host. Separate because the contents are
//                            the panels themselves: importing it is opting in
//                            to their weight.
//
// COMPATIBILITY
// -------------
// `VIEWER_CORE_API_VERSION` is bumped on a breaking change to anything exported
// from the entry points — the same contract `PLUGIN_API_VERSION` provides
// for the slot interfaces. A shell declares the range it was built against via
// its plugin's `coreApiRange`.
//
// RULES FOR THIS FILE
// -------------------
//  * Re-export from LEAF modules only. In particular never from `@/plugins`
//    (the loader barrel): it imports `registry.generated`, which imports the
//    plugin packages, which import this facade — an import cycle that would
//    bite at module-init time.
//  * Adding an export is a contract decision, not a convenience: everything
//    here is something an out-of-tree repo may pin to.
//  * `src/__tests__/plugins/viewerCoreFacade.test.ts` enforces both rules plus
//    the "shells import only the facade" fence.

// Also bumped on an ADDITIVE export a shell can DEPEND on, for the reason
// `PLUGIN_API_VERSION` is: without a version to name in `coreApiRange`, a shell
// built against the newer facade fails on an older core at an undefined import,
// from inside the shell, with nothing naming the mismatch.
//
//   1.4.0  `useComputeStore` + `computeJobOptions` on `@/viewer-core/app`:
//          the user's compute settings (cores, memory) for server-side jobs.
//          Plus set isolation on `@/viewer-core/scene` — `applyFeaGroupVisibility`
//          and the `feaSets` arithmetic — so a shell and a result plugin scope
//          the scene through one path.
//   1.3.0  `notifyActiveModeSceneColor` + `sceneColorOwner` on
//          `@/viewer-core/scene`, with `PluginModeSpec.ownsSceneColor`: core
//          suspends/restores the active FEA field for a mode that paints its
//          own scene colouring (issue #308). Plus `AdminPanel` on
//          `@/viewer-core/app`, so a shell mounts the admin surface rather
//          than forking it.
//   1.2.0  derived result hierarchy, component/layer actions and unit helpers
//          on `@/viewer-core/scene`.
//          Plus `CanvasWrapper`'s `legend` prop, so a shell that places the
//          legend itself can stop core mounting a second one, and
//          `feaValuesForElement` for reading a picked element as numbers.
//   1.1.0  `ExternalModelsPanel` + `useExternalModelsStore` on
//          `@/viewer-core/scene`. Both kept, DEPRECATED: the panel is gone
//          (the Sources tab replaces it) and the export now renders nothing.
//
// Out of sequence, because the list above runs newest-first and this is the
// newest entry:
//
//   1.5.0  `canOpenInScene` on `@/viewer-core/app`, and the
//          `RenderableFileProvider` slot types below. A shell that offers files
//          — a storage browser, a gallery — must ask that ONE predicate rather
//          than compose `isStreamingFEAResult || canLoadIntoSceneLegacy` for
//          itself, because the answer now also depends on what plugins have
//          registered and a shell composing it by hand would silently hide
//          their files. Kept in step with PLUGIN_API_VERSION 1.5.0, which is
//          the same capability seen from the plugin's side.
//   1.7.0  `localShutdownAvailable` + `stopLocalViewer` on `@/viewer-core/app`:
//          a shell offers "stop the local viewer" on a viewer a launcher
//          started with a shutdown token, and nowhere else. 1.6.0 is skipped
//          on purpose: PLUGIN_API_VERSION already is 1.6.0 for a different
//          capability, and a shell's `coreApiRange` is checked against THAT
//          number, so this entry takes the next one both can share.
//   1.8.0  Shared helpers a result plugin used to copy, on `@/viewer-core/app`:
//          `formatNumber`, the banded `ColorScale` (`scaleColor`, `cssScaleColor`),
//          `readSidecarElementFieldStep` / `readSidecarElementFieldEnvelope` (AFEL
//          blobs in a plugin's own sidecar, through core's reader),
//          `withCacheBust`, `registerJobFinisher`, the cross-window simulation
//          sync (`postSim`, `subscribeSim`, `followerParams`, `followerUrl`) and
//          `CollapsibleSection`. Ships with PLUGIN_API_VERSION
//          1.9.0 (the renderer-neutral scene overlays and `showColorScale`).
//   1.9.0  The `SceneBox` and `SceneFace` types of plugin API 1.10.0's view verbs
//          (`frameBox`, `findFaces` / `focusFace`).
//   1.10.0 The panel registry and the panel intents, here on the root entry:
//          `registerPanel` / `listPanels` / `getPanel` / `listToolbarPanels`,
//          `reveal` / `hide` / `toggle` / `isPanelOpen`, `bindStoreFlag`, and the
//          panel and layout types. One catalogue of every panel core and the
//          plugins offer, each bound to the visibility flag it already has, so a
//          shell lists panels and opens them by id instead of rediscovering them.
//          Additive. Ships with PLUGIN_API_VERSION 1.12.0.
//   1.11.0 The `@/viewer-core/layout` entry: core's panel CONTENTS, split from
//          their classic frames (`SceneBody`, `ObjectInfoBody`, `StorageBody`,
//          `OptionsBody`, `SimulationBody`, `CellBuilderBody`, ...), the shared
//          panel chrome (`PanelCard`, `PanelFrame`, `WindowFrame`,
//          `FloatingWindow`, `SheetHandle`, `useBottomSheet`, the named
//          breakpoints), and the layout host with its store and pure reducers.
//          On the root: `PanelSpec.content` (a panel's frameless content) and
//          `PanelSpec.watch` (when `available` may change), `watchStores`, and
//          `PanelFrameProps.dragHandleClassName`. Additive.
//   1.12.0 Undocking, on `@/viewer-core/layout`: the reducers `floatPanel`,
//          `setFloatRect`, `dockPanel`, `minimisePanel`, `restorePanel`; the
//          header-action slot of a panel's own frame (`PanelHeaderActions`,
//          `PanelHeaderActionsProvider`, `usePanelHeaderActions`) and its glyphs;
//          `localLayoutPersistence`; `FloatingWindow`'s caller-kept `rect` /
//          `onRectChange`. Additive.
//   1.13.0 Undocking, continued, on `@/viewer-core/layout`: the per-user server
//          backend `serverLayoutPersistence` (debounced, with a browser-local
//          fallback) and its `PreferenceStore`; the zone keys (`ZoneKeys`:
//          F6 / Shift+F6 between zones marked `ZONE_ATTR`, Esc on a floating
//          window, `useFloatFocus` / `restoreFocus`); an optional
//          `panelId` on `PanelHeaderActionsProvider` and `WindowHeaderActions`;
//          drag-to-dock (`moveDockDrag` / `endDockDrag` hit-testing drop targets
//          marked `DROP_ATTR`, `dockDragStore`) and `FloatingWindow`'s
//          `onDragMove` / `onDragEnd`; `OverlayPortal` / `TransformedHost`
//          (a fixed overlay of content in a floating window portals to the
//          body). On `viewerApi`: `getPreference` / `setPreference`.
//          Options on core's panel contents, so a shell uses them instead of a
//          copy. On `@/viewer-core/layout`: `SceneTreeBody` (the Scene tree) with
//          `SceneTreeProps` (`rootFilter`, `rootFilterLabels`, `extraRoots`, `onSelectOverride`,
//          `header`) and `TreeNodeData.meta`; `TreeBody`'s `sceneTree`;
//          `SceneBodyProps` (`tabs`, `hiddenTabs`, `femTab`, `arrangement`,
//          `loadedTitle`, `loadedEmpty`); `ObjectInfoBodyProps` (`onShowInData`)
//          and `CORE_PROPERTY_PROVIDERS`. On the root: `registerSceneTreeRoots` /
//          `listSceneTreeRoots`, the rows a plugin adds to the Scene tree, and
//          `registerPropertyProvider` / `listPropertyProviders` / `providersFor`,
//          the sections a plugin adds to the Selected Object panel.
//          Simulation: `SimulationBodyProps` (`layout: "panel" | "inline"`,
//          `showWarpToggle`, `showBeamSolids`, `showColorToggle`,
//          `excludeDockedTabs`: a dock named in `dock` or as `region`) and
//          `AnimationControls`. `ConvertBodyProps` (`inViewer`), and the progress
//          toast `ConversionProgress` with `isClaimed` (loads a host reports
//          elsewhere). The node editor's toolbar actions `request_list_of_nodes`
//          and `start_new_node_editor`.
//          Core's own keys in the shortcut registry: `CORE_SHORTCUTS` /
//          `CORE_SHORTCUT_GROUPS` (every global viewer key as a `core:` entry; the
//          keys a widget keeps for itself are marked `local`), listed by
//          `listShortcuts`, with `RESERVED_SHORTCUTS` derived from them;
//          `CommandSpec.shortcut` (a command shows the chord of the core shortcut
//          it names, in place of a hand-written `chordLabel`), `coreShortcutKeys`,
//          `installShortcutDispatcher` (the one shared keydown listener) and
//          `RegisteredShortcut.local`. Additive.
//          The shared `CollapsibleSection` (its `"section"` and `"caret"`
//          looks), on `@/viewer-core/layout`. Additive.
//          Rearranging panels. On the root: `PanelAction.menu` (`PanelMenuItem`),
//          a header action that opens a menu. On `@/viewer-core/layout`:
//          `WindowHeaderMenuButton` and `MoveGlyph`; indexed drop targets
//          (`DROP_INDEX_ATTR`, `DROP_AXIS_ATTR`, `dropTargetAt`, `endDockDragAt`,
//          `DropTarget`, `DockDragState.index`); the zone kind `"dock"`;
//          `dragHandleClassName` on a docked `PanelFrame` marks the header a
//          host drags the panel by.
//          In-app dialogs on the root: `confirmDialog` / `alertDialog` /
//          `promptDialog` (promise-based; core's storage panel asks through them
//          instead of `window.confirm` / `alert` / `prompt`) and `setDialogHost`,
//          which installs a host's own.
//          Storage: `StorageBodyProps` (`rowLoadProgress`, `selectionActions:
//          "toolbar" | "rowMenu"`, `onConvert`, `onClose`, `scopePicker`,
//          `showModified: boolean | "auto"`) and `STORAGE_MODIFIED_MIN_PX`; its
//          `maximized` / `setMaximized` are kept, ignored and deprecated (the panel no
//          longer maximizes). On the root, the load claims the progress toast
//          consults by default: `claimLoad` / `releaseLoad` / `isLoadClaimed`,
//          `subscribeLoadClaims`, `loadClaimsVersion`.
//          Cellbuilder: `CellBuilderBodyProps` (`tabs`, `tabLabels`, `showUndoRedo`,
//          `showAddButtons`, `showComponentList`, `emptyState`), also taken by
//          `CellBuilderContent`, and `CellBuilderTab`. Additive.
//          The split grid of the classic preset, in place of a separate docked
//          preset: `LayoutDoc.grid` (`LAYOUT_DOC_VERSION` 2; an older document
//          starts over). On `@/viewer-core/layout`: the pure tree (`GridNode`:
//          `SplitNode`, `GroupNode`, `ViewportNode`; `dockInGrid`,
//          `removeFromGrid`, `setActiveTab`, `setSplitSizes`, `equaliseSplit`,
//          `resizePair`, `shownGrid`, `normaliseGrid`,
//          `gridGroups`, `gridPanels`, `groupOf`, `findNode`, `splitShare`,
//          `DEFAULT_GRID`, `VIEWPORT_NODE`, `ROOT_NODE`); the document reducers
//          `dockInGridDoc`, `updateGrid`, `gridOf`, `isInGrid`, `floatPanelAt`
//          (`floatPanel`, `dockPanel` and `closePanel` also take a panel out of
//          the grid); edge drop targets (`DROP_EDGES_ATTR`, `edgeAt`,
//          `DropTarget.edge`, `DockDragState.edge`, `cancelDockDrag`); a
//          panel's own window given actions and drag-to-dock by its host
//          (`WindowDockProvider`, `useWindowDock`, `WindowDockActions`,
//          `windowDockAttrs`; `FloatingWindow` and `WindowFrame` pick it up);
//          `FloatLayerProps.skip` and `windowDock`. `DOCKED_PRESET_ID` stays
//          a placement key only.
//          Composed shells: `UiShellSpec.load` is optional. Without it core
//          mounts its own root (routes, sign-in, viewer provider) and its layout
//          of floating and dockable panels for the shell, arranged by
//          `UiShellSpec.layout` (`UiShellLayout`: a starting `grid`, panels to
//          `open` when it loads), dressed by `chrome` (`UiShellChrome`:
//          a lazy `header`, `toolbar`) and `themeTokens`; each shell keeps its own
//          layout document (`LayoutDoc.context` is the shell id). `isComposedShell`;
//          `CORE_PANEL_IDS` / `CORE_PANEL_OWNER` / `CorePanelId` and the grid types
//          on the root. On `@/viewer-core/layout`: `PanelToggles` (the top bar's
//          toggles, for a shell's header), `LayoutHostProps.context`, and
//          `LayoutStoreState.setStartingDoc`.
//          Plugin panels in the layout: a slot that stands on its own (region
//          `top-panel`, or any slot that names a `dock`) has frameless content, so
//          it docks in the split grid and floats like a core panel; while the
//          layout shows it there, its region host leaves it out (its top-bar
//          button stays). `UiShellLayout.docks` maps a dock id to a group of the
//          shell's grid; a dock-naming plugin panel is placed there once.
//          Modes: `UiShellLayout.modes` (`UiShellMode`), each with its own
//          arrangement and saved document (context `<shell>/<mode>`); plugins'
//          modes (`PluginSpec.modes`) follow the shell's. A panel naming `modes`
//          shows only in those. On the root: `listShellModes`, `activeShellMode`,
//          `setShellMode`, `subscribeShellModes`, `shellModesVersion`,
//          `offeredInMode`, `ShellMode`. On `@/viewer-core/layout`:
//          `ShellModeSwitcher`, `ShellModeToolbar` (a plugin mode's `toolbar`),
//          `useShellMode`. A mode change is reported to the scene colouring
//          (`notifyActiveModeSceneColor`) by core.
//          Window and group chrome read `--ada-window-*`, `--ada-tab-*` and
//          `--ada-group-divider` (falling back to the classic colours), so a
//          shell's `themeTokens` restyle them; `GROUP_HEADER`, `groupTabClass`
//          and `GROUP_DIVIDER` on `@/viewer-core/layout`. A dormant plugin's panels
//          are unavailable (`setPluginContextFactory`, set by the bootstrap).
//          Shortcuts on the root: `registerShortcut` / `unregisterShortcut` /
//          `listShortcuts` / `parseShortcut` (`ShortcutSpec`, `"Shift+K"`
//          chords); core's own keys are `RESERVED_SHORTCUTS` and refused. A
//          non-core panel's `PanelSpec.shortcut` toggles it. The layout dispatches
//          them and the shortcuts dialog lists them.
//          Pop-out windows: a panel with `capabilities.popout` that needs no
//          canvas offers "Open in a new window": `?popout=<id>` boots canvas-less
//          with that panel alone. On the root: `POPOUT_PARAM`, `popoutPanelId`,
//          `popoutUrl`, `canPopOut`, `openPopout`; `PanelSlot.popout` for a plugin.
//          On `@/viewer-core/app`: `AppRoot`, core's root (routes, sign-in, deep
//          links) for a shell that keeps `load`.
//          Storage: `StorageBodyProps.fillHeight` (the file list takes its host's
//          height; core sets it where the layout sizes the panel);
//          `ConversionProgressProps.isClaimed` now adds to core's load claims
//          instead of replacing them. On `@/viewer-core/app`: `requestScopeChange`,
//          `applyScopeChange`, `sceneHasModels`.
//          Docked groups, so a docked UI is a configuration: `GroupNode` /
//          `SplitNode` `fixed` (`FixedSize`: px with min / max, kept on window
//          resize; dividers beside it resize it in px), `GroupNode.collapsed` (a
//          `COLLAPSED_PX` strip of panel icons that restores it) and
//          `arrangement` (`GroupArrangement`: tabs, stack, or auto by height); the
//          reducers `setFixedPx`, `clampFixed`, `setGroupCollapsed`,
//          `setGroupArrangement`; the grid honours `PanelConstraints.minSize`.
//          `UiShellChrome.groups` (`actions`: add a panel here, collapse, pin;
//          `tabIcons`), `footer` and `rail`. `PanelCapabilities.fill` (content
//          edge to edge) and `pinnable`; pinned panels (`LayoutPrefs.pinned`, per
//          shell) stay placed across modes. `PanelAction.pressed`, `AddGlyph`,
//          `PinGlyph`. Modes: `UiShellMode.ownsSceneColor` / `showsDeformation` /
//          `toolbar` (and `PluginModeSpec.showsDeformation`), `?mode=`
//          (`SHELL_MODE_PARAM`), badges (`setShellModeBadge`, `shellModeBadge`,
//          `ShellModeBadge`). `themeTokens` may be a function of the user's theme.
//          `UiShellSpec.panels` (`PanelOverride`: title, icon, hint, shortcut,
//          modes, capabilities, and `options` for the frameless content, or a
//          function of the mode) changes panels a shell does not own, core's
//          included; `PanelProps.options`, `RegisteredPanel.options`.
//          Commands on the root: `registerCommand` (`CommandSpec`: label, group,
//          keys, `when`, `disabledReason`, `checked`), `unregisterCommand`,
//          `runCommand`, `getCommand`, `listCommands` (core adds one per panel and
//          per mode), `matchCommand`; on `@/viewer-core/layout`: `MenuBar`
//          (`MenuDef` of command ids), `CommandPaletteHost`, `openCommandPalette`,
//          `closeCommandPalette`; `UiShellChrome.commandPalette`. HUDs:
//          `registerViewportOverlay` at a `ViewportAnchor`,
//          `UiShellChrome.overlays` (core's legend, `overlay`-dock plugin panels).
//          `UiShellMode.viewportCover`, `UiShellChrome.pageHeader`,
//          `UiShellLayout.version` / `migrate` (`LayoutDoc.shellVersion`), and on
//          `@/viewer-core/layout` `resetArrangement`, `saveWorkspace`,
//          `loadWorkspace`, `deleteWorkspace`, `listWorkspaces`
//          (`LayoutPrefs.workspaces`). Core's convert and admin panels take content
//          options.
//          Body options: `SimulationBodyProps.builtinTabs` (false: plugin tabs only),
//          `CellBuilderBodyProps.showFooter`, `OptionsBodyProps` (`sections`,
//          `hiddenSections`: `OptionsSectionId`), `SceneTreeProps.rowExtras` /
//          `rowClassName` (`SceneTreeRowOptions`).
//          Body colours: `UiShellSpec.bodyPalette` redefines Tailwind's palette
//          variables inside elements marked `PANEL_BODY_ATTR` (column cards, window
//          bodies, grid groups, floats, covers, pop-out pages), so every core body
//          takes a shell's colours with no class changed; `panelBodyAttrs`,
//          `bodyPaletteCss` on `@/viewer-core/layout`. `UiShellChrome.toastAnchor`
//          and `ConversionProgressProps.anchor` (`ToastAnchor`).
//          Shell configuration gaps: `UiShellLayout.defaultMode` (the mode with no
//          `?mode=` and nothing stored); `PanelOverride.available` (false, or a
//          function of the mode, hides a panel from the lists, toggles, commands
//          and add menus) and `PanelOverride.bodyPalette` (variables scoped to one
//          panel's body: `PANEL_ID_ATTR`, `panelBodyAttrsFor`, and `bodyPaletteCss`
//          takes a per-panel map); a composed shell may dock the sections the
//          classic UI draws inside another panel (the result data table), which
//          then leave their host; `themeTokens` and `bodyPalette` also apply on
//          the canvas-less pages (`/convert`, `/admin`); `PanelBodyPalette` on
//          `@/viewer-core/layout` gives core bodies drawn outside panels (dialogs,
//          flyouts) the shell's body palette; `MenuItemDef` may hold a submenu
//          (`{label, items}`, nesting; `offeredMenuItems`); `CommandSpec.chordLabel`
//          (`commandChord`) shows a chord in the palette and menus without binding
//          it, for keys core owns; a group narrower than `COMPACT_GROUP_PX` folds
//          its header actions into a "more" menu (`MoreGlyph`), close stays in
//          the bar. Behaviour change: `PluginModeSpec.showsDeformation` now
//          defaults to false for a plugin's mode (opt in with true); a shell's own
//          modes still default to shown. `reveal(id)` of an unbound panel already
//          docked or floating brings it forward where it is.
//          Multi-window dashboards: a pop-out window (`?window=<id>`) holds a
//          split grid of panel groups of its own (tabs, stacks, splits, drag to
//          dock; no view), kept per window id (context `window/<id>`);
//          `?popout=<id>` opens a new one holding that panel. The Move panel menu
//          offers "Move to window" (the other open windows, "New window");
//          `PanelMenuItem.items` is a submenu (or a function giving it). The
//          windows talk over a window bus, on the root: `createWindowBus`
//          (`WindowBus`: presence by hello / announce / heartbeat / goodbye,
//          `windows()`, `setPanels`, `setLabel`, `onGone`), transports
//          (`WindowBusTransport`, `broadcastTransport`, `memoryTransportHub`,
//          `nullTransport`, `windowBusChannelName(path, shell, session)`),
//          dashboard SESSIONS -- one per main window, so two main windows never
//          mix (`newSessionId`, `isSessionId`, `claimSession`, `resolveSessionId`;
//          `SESSION_PARAM` / `popoutSession` on the URL) --, `MAIN_WINDOW_ID`, the move
//          handshake `sendPanel` / `acceptPanels` (`MOVE_ACK_MS`), `panelsToReturn`,
//          the dashboard record (`DashboardRecord`, `recordWindow`,
//          `forgetWindow`, `readDashboard`, `closedDashboardWindows`),
//          `nextWindowId`; window URLs on `./popout` (`WINDOW_PARAM`,
//          `popoutWindowId`, `isPopoutPage`, `windowUrl`, `windowTarget`,
//          `openWindow`); `setRevealElsewhere`. The main window hides a panel
//          another window shows, reveals it there instead, takes back the panels
//          of a window that closes, and offers "Reopen windows"
//          (`REOPEN_WINDOWS_COMMAND`). Mirroring: `PanelCapabilities.mirrored`
//          (`PanelSlot.mirrored`) lets a scene panel go to a pop-out window, fed
//          by `registerMirrorTopic` snapshots the main window publishes and
//          acting by `registerMirrorCommand` commands it runs (`hostMirror`,
//          `followMirror`, `sendMirrorCommand`); core mirrors the selection for
//          the object info panel (now `mirrored`). On `@/viewer-core/layout`:
//          `windowBus`, `useOpenWindows`, `useThisWindow`, `useMainWindowLive`,
//          `moveToWindowItem`, `openNewWindow`, `installMainWindow`,
//          `reopenWindows`, the window-document helpers (`dockInWindow`, ...),
//          `GridOps` / `GridOpsProvider` / `GridView`, `PopoutPage`; the session
//          runtime (`dashboardSession`, `ensureDashboardSession`, `pruneSessions`,
//          the session-keyed `windowLayoutKey` / `windowLabelKey` /
//          `dashboardKey`, `lastDashboardKey`). Each main window has its own
//          session (id in its tab's sessionStorage; a duplicated tab takes a new
//          one), so two main windows never share pop-outs, moves or mirroring;
//          pop-outs carry it in `?session=`, one without is standalone; storage
//          is keyed by session, the last dashboard can be reopened (adopted)
//          from a new session, old sessions are pruned. Behaviour change:
//          `canPopOut` also admits `mirrored` panels; `openWindow` returns the
//          window's handle and takes the session; `windowBusChannelName`,
//          `windowUrl`, `popoutUrl` and `windowTarget` take a session.
//          The built-in Desktop UI: a composed shell core registers in every
//          build (`DESKTOP_UI_SHELL_ID`, `desktopUiShellSpec`; `?ui=desktop`),
//          with a menu bar, docked panels and a status bar; the classic UI stays
//          the default. Core's commands, registered for every composed shell
//          (`CORE_COMMAND_IDS`, `themeCommandId`, `uiShellCommandId`,
//          `workspaceCommandId`): upload, undo / redo, selection and visibility,
//          camera fits, display toggles, theme, UI switch, full screen, new
//          window, layout reset and workspaces, the shortcuts dialog. On
//          `@/viewer-core/layout`: `desktopMenus` / `menuCommandIds` (the Desktop
//          UI's menu model) and `openShortcutsDialog`. Behaviour change:
//          `offeredMenuItems` drops separators left at either end or doubled.
//          Scene panels act from pop-outs and the scene tree is mirrored:
//          `MirrorTopic.delta` (sent on a change instead of the snapshot) and
//          an `apply` that returns `false` (the window asks for the snapshot
//          again); the dependency-free `sceneTreeTopic` (a tree sent flat and
//          in chunks on a tree change only, visibility and selection as deltas
//          otherwise; `flattenTree`, `buildTree`, `ROW_VISIBILITY_CODES`,
//          `SCENE_TREE_CHUNK`). On `@/viewer-core/layout`: the act seam
//          `useSceneActions` / `sceneActions` / `remoteSceneActions` /
//          `isMirroredWindow` (`SCENE_ACTION_COMMANDS`, `MIRRORED_CORE_COMMANDS`),
//          core's `core:scene-tree` topic (`SCENE_TREE_TOPIC`,
//          `coreSceneTreeSource`, `coreSceneTreeSink`, `useSceneMirrorStore`).
//          Behaviour change: the selection tree panel is `mirrored`;
//          `core:selection` also carries `rangeCount` and `parentName`.
//          The layout engine is the only path: the built-in UI is the classic
//          preset, and the hand-written tree it replaced is gone. Behaviour
//          change: `?layout=classic` and `localStorage["ada:layout-engine"]` no
//          longer switch anything. DEPRECATED, kept (released in 1.12.0, on
//          `@/viewer-core/layout`): `layoutEngineEnabled` (always true),
//          `LAYOUT_ENGINE_PARAM`, `LAYOUT_ENGINE_STORAGE_KEY`. DEPRECATED, kept:
//          the store flags a panel's binding wraps (`isOptionsVisible`,
//          `show_info_box`, `show_scene_info_box`, `isControlsVisible`,
//          `showServerInfoBox`, `isTreeCollapsed`, ... and their setters and
//          toggles): open, close and ask through `reveal` / `hide` / `toggle` /
//          `isPanelOpen` with the panel's id, which the layout controls.
//          The in-viewer admin / convert window, the attribute search's and the
//          node editor's windows are `TransformedHost`s, so the fixed overlays
//          inside them (`OverlayPortal`) open over the page.
//   1.14.0 Core commands for what the Desktop UI's menus lacked, on the root's
//          `CORE_COMMAND_IDS`: `openFile` (File -> Open…: GLB / glTF from disk
//          straight into the scene, no upload, in any viewer; other formats are
//          uploaded and converted on a converting REST server, and refused with
//          the reason elsewhere; files dropped on the 3D view open the same way,
//          added to the scene), `selectAll` / `invertSelection` (the shown
//          elements, one undo entry each), the named camera views (`viewTop` ...
//          `viewIsometric`; `CAMERA_VIEWS`, `cameraViewCommandId`, `CameraView`),
//          `exportSelection` / `exportModel` (the server's STEP / IFC export in a
//          dialog of scope and format; REST viewer) and `about`. Core shortcuts
//          `core:select-all` (Ctrl+A) and `core:open-file` (Ctrl+O), so both are
//          now in `RESERVED_SHORTCUTS`. On `@/viewer-core/layout`:
//          `openCoreDialog` / `closeCoreDialog` (`CoreDialogRequest`,
//          `ExportScope`), drawn by a composed layout. Additive, apart from the two
//          reserved chords.
//          UI-shell switches keep the scene. Between core's own UI and composed
//          shells `setActiveUiShell` switches IN PLACE (no reload): the viewer,
//          its canvas, the loaded models, the selection, the camera and every
//          store stay, and the shell's layout, chrome, modes, panel overrides,
//          theme tokens and body palette are swapped; the shell switched away
//          from loses its palette command and, for a plugin's shell, what its
//          plugin registered while it was up (commands, shortcuts, HUDs), given
//          back when it returns. A shell with `load`, or one that sets the new
//          `UiShellSpec.reloadOnSwitch`, is still switched by a reload, which
//          carries a deep link (`carryOnShellSwitch`, `shellSwitchUrl`,
//          `ShellSwitchCarry`) and restores the stored models, camera, hidden
//          objects and selection through the scene restore, even with that
//          setting off (`loadedByShellSwitch`). On the root: `switchesInPlace`,
//          `canSwitchInPlace`, `subscribeActiveUiShell`, `activeUiShellVersion`,
//          `bootUiShellId` (the shell the page booted in; a multi-window
//          dashboard stays that shell's through an in-place switch, and a
//          pop-out it opens boots into it). Behaviour change: a switch between
//          core's UI and the Desktop UI no longer reloads the page.
//          The command palette is core's, in every UI: core's root mounts it
//          once (`CorePaletteHost` on `@/viewer-core/layout`), core's shortcut
//          `core:command-palette` (Ctrl+K, now in `RESERVED_SHORTCUTS`) opens it,
//          core registers its command, and core's own UI gets core's commands
//          (no button: the shortcuts dialog lists Ctrl+K). `UiShellChrome.commandPalette` may be `false` (off
//          while the shell is active); its `keys` add a chord beside Ctrl+K.
//          Behaviour change: a composed shell no longer mounts a palette of its
//          own, and the palette is on unless a shell turns it off. "Reset every
//          mode's layout" is greyed, with the reason, in a UI without modes.
//          "Customisable panels" is per UI shell (`LayoutPrefs.customisablePanelsByShell`;
//          the one setting of before, `LayoutPrefs.customisablePanels`, is moved to
//          core's own UI's on load) and REQUIRED by a composed shell
//          (`UiShellLayout.customisablePanels`: "required", the default, or
//          "optional"): always on there, the Options toggle disabled with the
//          reason. Every reader asks one selector, on `@/viewer-core/layout`:
//          `customisablePanels`, `customisablePanelsRequired`,
//          `setCustomisablePanels`, `useCustomisablePanels`,
//          `useCustomisablePanelsRequired`; `LayoutStoreState.setCustomisablePanels`
//          takes the shell id. Behaviour change: turning the setting off in core's
//          UI no longer breaks the Desktop UI's docked layout, and is kept
//          for core's UI across a switch.
//          FEA sets and properties in core, for every UI: while an FEA result is
//          loaded the Scene tree gets "Sets" (element and node sets, with counts)
//          and "Properties" (each property field's values, the elements carrying
//          each read on demand) after the models; selecting rows isolates their
//          union, with the rest hidden or drawn as its element edges, one undo
//          entry per change (`ViewerActionKind` `isolate-sets` / `clear-isolation`
//          / `context-wireframe`). `TreeNodeData.informational` / `marked` /
//          `title`. Core commands `feaIsolateSets`, `feaIsolateSelectionSets`,
//          `feaClearIsolation`, `feaToggleContextWireframe` (the Desktop UI's
//          View -> FEA sets), the scene actions `feaIsolate`,
//          `feaIsolateMatching`, `feaClearIsolation`, `feaToggleContext`,
//          `feaHideRows`, `feaShowRows` (mirror commands `core:fea-*`), the mirror
//          topic `core:fea-tree`, the row menu on FEA rows (a right-click there
//          no longer selects the row), and the Selected Object panel's `fea-sets`
//          section in `CORE_PROPERTY_PROVIDERS`. On `@/viewer-core/scene`: the
//          rows' pure builders and ids (`buildFeaTreeRoots`, `buildSetsRoot`,
//          `buildPropertyRow`, `elementSetRowId`, `propertyValueRowId`,
//          `isolatedMembers`, `combineIsolation`, ...), the actions
//          (`isolateFeaRows`, `isolateFeaSets`, `isolateFeaSetsMatching`,
//          `clearFeaIsolation`, `toggleFeaContextWireframe`, `hideFeaRows`,
//          `showFeaRows`), what they read (`feaTreeView`, `subscribeFeaTreeView`,
//          `feaIsolationReason`, `feaContextReason`, `feaPickedInfo`,
//          `ensurePropertyMembership`, `useFeaIsolationStore`) and
//          `applyFeaIsolationOverlays`. Additive.
//          A marking menu in core: a wheel of up to eight commands (and a short
//          list under it) at the pointer over the 3D view, opened by holding the
//          right button still for 300 ms (a quick right click is still the Scene
//          menu, a right drag still pans) or by core's `core:marking-menu`
//          (Shift+E, now in `RESERVED_SHORTCUTS`); flick and release, or click,
//          or arrows and Enter. `UiShellChrome.markingMenu` (`MarkingMenuConfig`:
//          `items` / `more`, as lists or functions of `MarkingMenuContext`,
//          submenus, `gesture`; `false`: none): on in a shell that gives it,
//          off in core's own UI unless the user turns it on (Options -> Theme;
//          `LayoutPrefs.markingMenuByShell`). Core commands `markingMenu`,
//          `toggleMarkingMenu`. On the root, the rules (`MarkingGesture`,
//          `sectorAt`, `resolveMarkingMenu`, `defaultMarkingMenuItems`, ...); on
//          `@/viewer-core/layout` the component (`MarkingMenuView`,
//          `MarkingMenuHost`, mounted by a composed layout in the main window)
//          and `openMarkingMenu` / `setMarkingMenuOn` / `servedMarkingMenu`.
//          Core's tool rail: `UiShellChrome.rail` may be `{tools}`
//          (`ToolRailConfig`: command ids, `RAIL_SEPARATOR`) in place of a lazy
//          component (still taken): icon buttons from the commands
//          (`CommandSpec.icon`; core's commands have icons now,
//          `CORE_COMMAND_ICONS`), the chord in the tooltip, pressed for a
//          checked toggle, greyed with the reason; the user adds, removes and
//          reorders tools (`core:customise-rail`, the rail's menu and button),
//          kept per shell in `LayoutPrefs.rail` (`RailPrefs`, with the tools a
//          later shell list adds put in: `railTools`). The Desktop UI has both
//          (`DESKTOP_RAIL_TOOLS`). Additive, apart from the reserved Shift+E.
//   1.15.0 Core mode toolbars: `UiShellMode.toolbars` names core's ready-made
//          toolbars for a mode's toolbar slot (`"playback"`, `"section"`,
//          `"representation"`; `CoreModeToolbarId`, `CORE_MODE_TOOLBAR_IDS` on the
//          root), drawn by `ShellModeToolbar` before the mode's own `toolbar`.
//          On `@/viewer-core/layout` the components (`PlaybackToolbar`,
//          `SectionToolbar`, `RepresentationToolbar`, `CoreModeToolbars`,
//          `CORE_MODE_TOOLBARS`) and `CommandButton` (a button over a command), for a
//          shell that mounts them in its own chrome. New core commands on
//          `CORE_COMMAND_IDS`: `playPause`, `stopResults`, `stepBack`, `stepForward`
//          (result playback), `sectionX` / `sectionY` / `sectionZ`,
//          `sectionToggle`, `sectionFlip`, `sectionClear`, and
//          `feaToggleUndeformed` / `feaToggleElementEdges` /
//          `feaToggleResultColors`, with icons; the Desktop UI's modes use the
//          toolbars and its View menu lists the commands. Additive. Ships with
//          PLUGIN_API_VERSION 1.15.0.
//   1.16.0 Number labels and colour by property. Core draws node and element ids
//          of a loaded FEA result over the view (`NumberLabels`, bounded and
//          thinned on a screen grid) and colours the scene by a property.
//          `CORE_COMMAND_IDS`: `labelsNodes`, `labelsElements`, `colorByNext`,
//          `colorByModel`, `colorByReset`. A new core mode toolbar id `"colorBy"`
//          (`ColorByToolbar`), and `"representation"` gains the label switches.
//          `chrome.overlays` takes `labels` (draw the labels) and `colorByLegend`
//          (an anchor for `ColorByLegend`). On `@/viewer-core/layout`:
//          `NumberLabels`, `ColorByToolbar`, `ColorByLegend`. A plugin adds a
//          colouring with `registerColorBySource` (`ColorBySource`; also
//          `applyColorBy`, `resetColorBy`, `colorBySources`, `useColorByStore`)
//          and `useLabelsStore` switches the labels, all on `@/viewer-core/scene`. The Desktop UI uses all of
//          it. Additive. Ships with PLUGIN_API_VERSION 1.16.0.
//   1.17.0 Build mode for the procedural builder. A new core mode toolbar id
//          `"build"` (`BuilderToolbar`, on `@/viewer-core/layout`): the open model's
//          name and revision, undo / redo, compile, compile in the browser,
//          commit and close. `UiShellMode.requires: "builder"` offers a mode only
//          while a procedural model is open (left out of the switcher, the Modes
//          menu and the palette otherwise; the shell falls back when it goes
//          away). `chrome.overlays.builder` mounts the builder's canvas menus and
//          gizmo HUD for a shell that docks the panel. `CORE_COMMAND_IDS`:
//          `builderUndo`, `builderRedo`, `builderCompile`, `builderRecompile`,
//          `builderCompileBrowser`, `builderCommit`, `builderClose`, each greyed
//          with its reason (no model, read-only, no server compile). The Desktop
//          UI gains a Build mode. Additive. Ships with PLUGIN_API_VERSION 1.17.0.
export const VIEWER_CORE_API_VERSION = "1.17.0";

// ---------------------------------------------------------------------------
// Plugin declaration. `registerPlugin` is how a package announces itself —
// including a shell, via `uiShells`. The slot HOSTS live in
// `@/viewer-core/plugins` (heavier import).
// ---------------------------------------------------------------------------
export {
  disablePlugin,
  getPlugin,
  getPluginModes,
  getRegisteredPlugins,
  PLUGIN_API_VERSION,
  registerPlugin,
} from "@/plugins/registry";
export type {
  ActivationPredicate,
  AdaPluginContext,
  PanelSlot,
  PluginDockId,
  PluginModeId,
  PluginModeSpec,
  PluginApiClient,
  PluginLogLevel,
  PluginRegion,
  PluginSpec,
  PluginTheme,
  RegisteredPlugin,
  RenderableFileProvider,
  ResultSidecarLoader,
  SceneBox,
  SceneColor,
  SceneColorFieldProvider,
  SceneColorFieldResult,
  SceneElementOutline,
  SceneElementPaint,
  SceneFace,
  SceneHandle,
  SceneLabel,
  SceneLabelOptions,
  SceneLines,
  SceneOverlay,
  SidecarFetcher,
  TopBarButtonSpec,
  UrlParamHandler,
} from "@/plugins/registry";

// ---------------------------------------------------------------------------
// UI shells — registration and resolution. The switcher COMPONENT lives in
// `@/viewer-core/app`; this half is data, so it stays dependency-free.
// ---------------------------------------------------------------------------
export {
  activeUiShell,
  activeUiShellId,
  activeUiShellVersion,
  bootUiShellId,
  buildDefaultUiShellId,
  canSwitchInPlace,
  carryOnShellSwitch,
  CORE_UI_SHELL_ID,
  getUiShell,
  listUiShells,
  loadedByShellSwitch,
  setActiveUiShell,
  shellSwitchUrl,
  subscribeActiveUiShell,
  switchesInPlace,
  UI_SHELL_STORAGE_KEY,
  UI_SHELL_URL_PARAM,
} from "@/plugins/uiShells";
export type {
  RegisteredUiShell,
  ShellSwitchCarry,
  UiShellChrome,
  UiShellLayout,
  UiShellMode,
  UiShellResolution,
  UiShellSpec,
} from "@/plugins/uiShells";
// A composed shell's modes (dependency-free; the switcher components are on `@/viewer-core/layout`).
export {
  activeShellMode,
  listShellModes,
  offeredInMode,
  setShellMode,
  shellModesVersion,
  subscribeShellModes,
} from "@/layout/shellModes";
export type { CoreModeToolbarId, ShellMode, ShellModeBadge } from "@/layout/shellModes";
export { CORE_MODE_TOOLBAR_IDS } from "@/layout/shellModes";
export { SHELL_MODE_PARAM, setShellModeBadge, shellModeBadge } from "@/layout/shellModes";
export type { PanelOverride } from "@/layout/registry";
export type { FixedSize, GroupArrangement } from "@/layout/splitTree";
// Commands (menus, palette, keys share them) and HUDs over the view (dependency-free).
export { commandChord, getCommand, listCommands, matchCommand, registerCommand, runCommand, unregisterCommand } from "@/layout/commands";
export type { CommandSpec, RegisteredCommand } from "@/layout/commands";
// Core's own commands, by id (registered for every composed shell), for a shell's menus.
export { CAMERA_VIEWS, CORE_COMMAND_IDS, cameraViewCommandId, themeCommandId, uiShellCommandId, workspaceCommandId } from "@/layout/coreCommandIds";
export type { CameraView, CoreCommandId } from "@/layout/coreCommandIds";
// The built-in Desktop UI (a composed shell core registers next to the classic one).
export { DESKTOP_RAIL_TOOLS, DESKTOP_UI_SHELL_ID, desktopUiShellSpec } from "@/layout/desktop/desktopShell";
// The marking menu's and the tool rail's rules (dependency-free; the components are on `@/viewer-core/layout`).
export {
  MARKING_DEAD_ZONE_PX,
  MARKING_DIRECTIONS,
  MARKING_HOLD_MS,
  MARKING_MENU_MAX_SECTORS,
  MARKING_MOVE_TOLERANCE_PX,
  MARKING_RADIUS_PX,
  MARKING_VIEWS_SUBMENU,
  MarkingGesture,
  defaultMarkingMenuItems,
  defaultMarkingMenuMore,
  directionOf,
  markingGestureOptions,
  markingMenuAvailability,
  resolveMarkingMenu,
  sectorAt,
  sectorOffset,
  stepSelection,
} from "@/layout/markingMenuModel";
export type {
  MarkingDirection,
  MarkingGestureOptions,
  MarkingMenuConfig,
  MarkingMenuContext,
  MarkingMenuGesture,
  MarkingMenuItemDef,
  MarkingRelease,
  MarkingSubmenu,
} from "@/layout/markingMenuModel";
export {
  RAIL_SEPARATOR,
  addRailTool,
  dedupeRail,
  isRailSeparator,
  migrateRailPrefs,
  moveRailTool,
  railConfig,
  railIsDefault,
  railTools,
  removeRailTool,
  tidyRail,
} from "@/layout/toolRailModel";
export type { RailPrefs, ToolRailConfig } from "@/layout/toolRailModel";
export { VIEWPORT_ANCHORS, registerViewportOverlay, unregisterViewportOverlay } from "@/layout/viewportOverlays";
export type { ViewportAnchor, ViewportOverlaySpec } from "@/layout/viewportOverlays";
// Shortcuts a shell or plugin adds; core's own keys are reserved (dependency-free).
export {
  RESERVED_SHORTCUTS,
  coreShortcutKeys,
  listShortcuts,
  parseShortcut,
  registerShortcut,
  unregisterShortcut,
} from "@/layout/shortcuts";
export type { RegisteredShortcut, ShortcutKeys, ShortcutSpec } from "@/layout/shortcuts";
export { CORE_SHORTCUTS, CORE_SHORTCUT_GROUPS } from "@/layout/coreShortcutTable";
export type { CoreShortcutDef } from "@/layout/coreShortcutTable";
export { installShortcutDispatcher } from "@/layout/shortcutDispatch";
// A panel in a browser window of its own (dependency-free).
export {
  POPOUT_PARAM,
  SESSION_PARAM,
  WINDOW_PARAM,
  canPopOut,
  isPopoutPage,
  openPopout,
  openWindow,
  popoutPanelId,
  popoutSession,
  popoutUrl,
  popoutWindowId,
  windowTarget,
  windowUrl,
} from "@/layout/popout";
// State the main window mirrors to pop-out windows, and the actions they send back (dependency-free).
export {
  followMirror,
  hostMirror,
  listMirrorTopics,
  registerMirrorCommand,
  registerMirrorTopic,
  sendMirrorCommand,
} from "@/layout/windowMirror";
export type { MirrorCommand, MirrorTopic } from "@/layout/windowMirror";
// A tree mirrored in chunks, with visibility and selection deltas (dependency-free).
export {
  ROW_VISIBILITY_CODES,
  ROW_VISIBILITY_NAMES,
  SCENE_TREE_CHUNK,
  buildTree,
  flattenTree,
  sceneTreeTopic,
} from "@/layout/sceneTreeMirror";
export type {
  FlatTreeChunk,
  FlatTreeRoot,
  MirrorTreeNode,
  RowVisibilityName,
  SceneTreeMessage,
  SceneTreeSink,
  SceneTreeSource,
  SceneTreeTopicOptions,
} from "@/layout/sceneTreeMirror";
// Multi-window dashboards: the bus between the browser windows of one session (dependency-free).
export {
  CLAIM_WAIT_MS,
  HEARTBEAT_MS,
  LEAVE_GRACE_MS,
  MAIN_WINDOW_ID,
  MAIN_WINDOW_LABEL,
  MOVE_ACK_MS,
  STALE_MS,
  acceptPanels,
  broadcastTransport,
  claimSession,
  closedDashboardWindows,
  createWindowBus,
  defaultWindowLabel,
  emptyDashboard,
  findWindow,
  forgetWindow,
  isSessionId,
  isWindowBusMessage,
  isWindowId,
  mainWindowLive,
  memoryTransportHub,
  newSessionId,
  nextWindowId,
  nullTransport,
  panelsToReturn,
  readDashboard,
  recordWindow,
  resolveSessionId,
  sendPanel,
  windowBusChannelName,
  windowLabel,
  windowShowing,
} from "@/layout/windowBus";
export type {
  DashboardRecord,
  DashboardWindow,
  OutgoingWindowMessage,
  SendPanelOptions,
  WindowBus,
  WindowBusMessage,
  WindowBusMessageType,
  WindowBusOptions,
  WindowBusTransport,
  WindowGoneReason,
  WindowId,
  WindowInfo,
  WindowPresence,
} from "@/layout/windowBus";
export { isComposedShell } from "@/plugins/uiShells";
// Core's panel ids, for a composed shell's `layout` (dependency-free: the ids alone).
export { CORE_PANEL_IDS, CORE_PANEL_OWNER } from "@/layout/corePanelIds";
export type { CorePanelId } from "@/layout/corePanelIds";
export type { GridNode, GroupNode, SplitDir, SplitNode, ViewportNode } from "@/layout/splitTree";

// ---------------------------------------------------------------------------
// Panels (1.10.0) -- the catalogue of every panel, core's and plugins', and the
// intents that open and close one by id. Data and functions only, so it stays
// dependency-free; core's own panels are registered at bootstrap.
// ---------------------------------------------------------------------------
export {
  DEFAULT_PANEL_CAPABILITIES,
  placementFor,
} from "@/layout/panel";
export type {
  PanelAction,
  PanelMenuItem,
  PanelBinding,
  PanelBodyLoader,
  PanelCapabilities,
  PanelConstraints,
  PanelEnv,
  PanelFrameProps,
  PanelHostInfo,
  PanelId,
  PanelOwnerId,
  PanelPlacement,
  PanelPlacements,
  PanelProps,
  PanelSpec,
  PresetId,
  RegisteredPanel,
  ZoneId,
} from "@/layout/panel";
export {
  getPanel,
  isPanelAvailable,
  listPanels,
  listToolbarPanels,
  registerPanel,
} from "@/layout/registry";
export { hide, isPanelOpen, reveal, setRevealElsewhere, toggle } from "@/layout/reveal";
export type { PanelController } from "@/layout/reveal";
export {
  listSceneTreeRoots,
  registerSceneTreeRoots,
} from "@/plugins/sceneTreeRoots";
export type { SceneTreeRootProvider } from "@/plugins/sceneTreeRoots";
export {
  listPropertyProviders,
  providersFor,
  registerPropertyProvider,
} from "@/plugins/propertyProviders";
export type { PropertyProvider, SelectionSnapshot } from "@/plugins/propertyProviders";
export {
  alertDialog,
  confirmDialog,
  promptDialog,
  setDialogHost,
} from "@/plugins/dialogs";
export type {
  AlertOptions,
  ConfirmOptions,
  DialogHost,
  DialogText,
  DialogTone,
  PromptOptions,
} from "@/plugins/dialogs";
export {
  claimLoad,
  isLoadClaimed,
  loadClaimsVersion,
  releaseLoad,
  subscribeLoadClaims,
} from "@/plugins/loadClaims";
export { bindStoreFlag, watchStores } from "@/layout/bindings";
export type { FlagStore } from "@/layout/bindings";
export {
  CLASSIC_PRESET_ID,
  CLASSIC_ZONES,
  DOCKED_PRESET_ID,
  layoutDocKey,
} from "@/layout/layout";
export type {
  FloatState,
  LayoutDoc,
  LayoutPersistence,
  LayoutPrefs,
  LayoutPreset,
  ZoneDef,
  ZoneKind,
  ZoneState,
} from "@/layout/layout";
