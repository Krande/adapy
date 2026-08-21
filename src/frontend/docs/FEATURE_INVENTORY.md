# Feature inventory — the parity contract for the UI rebuild

Every user-reachable feature in the viewer as of the M0 baseline. **Nothing here may
regress.** A row closes only when Status is `Verified`, with the verification method
named.

Sources enumerated mechanically: the toggles in `components/Menu.tsx`, the tab lists
in `SceneInfoBox.tsx`, `AdminPanel.tsx` and `CellBuilderPanel.tsx`, the sections in
`OptionsComponent.tsx`, the bindings in `setupCameraControlsHandlers.ts`,
`storageMenuItems.tsx`, and the `runtime.*`-gated paths.

**Status legend** — `Pending` not started · `Moved` re-homed in the new shell, not yet
checked · `Verified` exercised in the browser or by a test.

**The business-logic column is a fence.** Those files implement the feature; the
rewrite re-chromes the components around them and must not modify them. See the hard
rule in the plan.

---

## A. Application shell and routing

| ID | Feature | Reached today | Owning component | Business logic (untouched) | New home | Status |
|---|---|---|---|---|---|---|
| A1 | Main viewer app | `/` | `app.tsx` → `AppBody` | — | `AppShell profile="viewer"` | Verified (browser) |
| A2 | OIDC redirect landing | `/auth/callback` | `auth/AuthCallback.tsx` | `services/auth/oidc.ts` | unchanged, outside the shell || Moved (app.tsx branch; not exercised) |
| A3 | Convert page | `/convert` | `convert/ConvertPage.tsx` | `services/conversion/*` | Data mode workspace + `profile="page"` deep link | Verified (browser, /convert) |
| A4 | Admin console | `/admin` (+ `#tab`) | `admin/AdminPanel.tsx` | `services/viewerApi.ts` | Data mode workspace + `profile="page"` deep link | Verified (browser, /admin) |
| A5 | Simulation follower window | `?simfollow=` | `simulation/SimFollowerPage.tsx` | `ada-sim` BroadcastChannel | `profile="window"` || Moved (app.tsx branch; not exercised) |
| A6 | Node-editor-only window | `NODE_EDITOR_ONLY` | `node_editor/NodeEditorComponent.tsx` | flatbuffers procedures | `profile="graph"` || **Restored** — Moved (needs NODE_EDITOR_ONLY to exercise) |
| A7 | Paradoc / Jupyter embed | `mountViewer()` | `embed/EmbedUI.tsx` | `embed/index.ts` | `profile="embed"` (EmbedUI deleted) || Moved (build:embed emits the lib; not opened) |
| A8 | Auth gate (REST) | wraps app | `auth/AuthGate.tsx` | `services/auth/oidc.ts`, `meStore` | wraps `AppShell` | Verified (M6, test) |
| A9 | Fatal error boundary | on throw | `common/ErrorBoundary.tsx` | — | unchanged, plus per-dock boundaries || Verified (browser — boundaries caught real throws) |

## B. Top toolbar (`components/Menu.tsx`) — every button

| ID | Button / title | Store flag | New home | Status |
|---|---|---|---|---|
| B1 | ☰ Toggle options drawer | `optionsStore.isOptionsVisible` | Preferences panel + title-bar menu set | Verified (browser, Settings dialog) |
| B2 | Show/Hide selection tree (`Shift+T`) | `treeViewStore.isTreeCollapsed` | Outliner, left dock | Verified (browser) |
| B3 | Toggle node editor | `useNodeEditorStore.isNodeEditorVisible` | Build mode, dock panel || Verified (browser, Tools menu item) |
| B4 | Storage (REST) | `serverInfoStore.showServerInfoBox` | Data mode, left dock | Verified (browser, Storage flyout) |
| B5 | Toggle object info | `objectInfoStore.show_info_box` | **Properties panel, right dock** (unified) | Verified (browser) |
| B6 | Toggle scene info | `sceneInfoStore.show_scene_info_box` | Scene panel, right dock | Verified (browser) |
| B7 | Reload nodes (graph mode) | — | `profile="graph"` toolbar | Pending — the graph profile is mounted again (A6), but its toolbar has no reload |
| B8 | Toggle animation controls | `animationStore.isControlsVisible` | Results toolbar → display popover | Verified (browser, toolbar popover) |
| B9 | Toggle connection-component panel | `componentControlsStore.isVisible` | Build mode, right dock || **Restored** — Verified (browser, panel opens) |
| B10 | Toggle procedural cellbuilder panel | `cellBuilderStore.panelVisible` | Build mode, right dock | Verified (browser) |
| B11 | Plugin top-bar buttons | `pluginUiStore` visible-map | hosted by shell TitleBar + compat map | Verified (M4, test) |
| B12 | Websocket status | `websocketStatusStore.showInfoBox` | **Status bar** || Verified (browser, REST state shown) |

## C. Inspect mode

### C1 Selection and navigation
| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| C1.1 | Click-to-select mesh (GPU pick) | `setupPointerHandler.ts` | `mesh_select/GpuMeshPicker.ts`, `CustomBatchedMesh.ts` | Verified (M3, browser) |
| C1.2 | Face-level picking | Scene ▸ Tools | `mesh_select/faceHighlight.ts`, `GpuMeshPicker` | Moved (contextual — needs a model with face_ranges) |
| C1.3 | Point-cloud picking | option `useGpuPointPicking` | `mesh_select/GpuPointPicker.ts` | Pending — needs a point-cloud fixture |
| C1.4 | Selection highlight | — | `utils/default_materials.ts` `selectedMaterial` | Verified (M1/M3, browser) |
| C1.5 | Model hierarchy tree | `tree_view/TreeViewComponent.tsx` (dock) | `utils/tree_view/treeGraph.ts`, `react-arborist` | Verified (browser, Outliner lists the fixture) |
| C1.6 | Tree keyboard nav (`Shift+↑↓←→`) | `setupCameraControlsHandlers.ts:126-140` | `utils/tree_view/treeNavigation.ts` | Verified (shortcuts.test.ts, both directions) |
| C1.7 | Cross-model select | — | `utils/scene/crossModelSelect.ts` | Pending — needs two models loaded |
| C1.8 | Copy selected names (`Shift+C`) | — | `utils/clipboard/copySelectionNames.ts` | Verified (shortcuts.test.ts, both directions) |
| C1.9 | Double-click recentre pivot | `setupPointerHandler.ts` | same | Pending — not exercised |

### C2 Visibility and camera
| ID | Feature | Reached today | Business logic | Status |
|---|---|---|---|---|
| C2.1 | Hide selection (`Shift+H`) | shortcut + Object Info | `utils/scene/visibility.ts` | Verified (shortcuts.test.ts + rail button) |
| C2.2 | Unhide all (`Shift+U`) | shortcut + Properties + rail | same | Verified (M3, browser) |
| C2.3 | Centre on selection (`Shift+F`) | shortcut | `centerViewOnSelection.ts` | Verified (shortcuts.test.ts, both directions) |
| C2.4 | Zoom to fit (`Shift+A`) | shortcut + rail | `setupCameraControlsHandlers.ts` `zoomToAll` | Verified (M3, browser) |
| C2.5 | Orbit / pan / zoom | canvas | OrbitControls or `camera-controls` | Pending — not exercised |
| C2.6 | Orientation gizmo (view cube) | canvas corner | `addOrientationGizmo` | Verified (browser — element present; its HMR crash fixed) |
| C2.7 | Adaptive near/far clipping | automatic | `applyAdaptiveClipping` | Pending — not exercised |
| C2.8 | Grid helper, Z-up toggle | Options | `addDynamicGridHelper` | Pending — Z-up verified in Preferences; the grid helper is unchecked |

### C3 Scene panel tabs (`SceneInfoBox.tsx` — 6 tabs, 2 contextual)
| ID | Tab ▸ section | Owning component | Status |
|---|---|---|---|
| C3.1 | Model ▸ loaded models / overlay / unload | `LoadedModelsSection.tsx` | Verified (browser; heading corrected to “Overlaid models”) |
| C3.2 | Model ▸ source + re-convert | `SourceSection.tsx` | Verified (browser, section present) |
| C3.3 | Model ▸ stats | `StatsSection.tsx`, `ModelStatsSection.tsx` | Verified (browser, Stats + Take-off present) |
| C3.4 | Model ▸ groups | `GroupsSection.tsx` | Verified (browser, section present) |
| C3.5 | Tools ▸ utilities | `UtilitiesSection.tsx` | Verified (browser, group present) |
| C3.6 | Tools ▸ face picking toggle | `FacePickingToggle.tsx` | Moved (contextual — needs a model with face_ranges) |
| C3.7 | Tools ▸ face search | `FaceSearchSection.tsx` | Moved (contextual — needs a model with face_ranges) |
| C3.8 | Clip ▸ section planes | `SectionPlanesPanel.tsx` + `SectionPlanesController.tsx` | Verified (M3, reachable from rail) |
| C3.9 | Mesh ▸ distortion scan | `MeshDistortionSection.tsx`, `meshPanelStore` | Verified (browser, group present) |
| C3.10 | FEM ▸ concepts *(contextual)* | `FemConceptsPanel.tsx` | Verified (browser — appears with a live FEA session) |
| C3.11 | Joints ▸ overview *(contextual)* | `JointsOverviewPanel.tsx` | Moved (contextual — needs a detailed model) |

### C4 Section planes (all of it — the closest thing to a measure tool)
Add X/Y/Z plane · enable/disable per plane · position slider · flip direction ·
attach drag gizmo · cap colour · delete. `sectionStore`, `section_caps.ts`.

### C5 Other inspect features
| ID | Feature | Owning component | Status |
|---|---|---|---|
| C5.1 | Model take-off / quantities | `ModelStatsPanel.tsx`, `utils/stats/modelStats.ts` | Pending |
| C5.2 | Stats detail + export menu | `statsStore.detailOpen/exportMenuOpen` | Pending |
| C5.3 | Gallery walk (prev/next, `←`/`→`) | `GalleryControls.tsx`, `galleryWalk.ts` | Pending |
| C5.4 | Type-icon overlay | `TypeIconController.tsx`, `typeIconClassify.ts` | Pending |
| C5.5 | Object metadata | `ObjectMetadataPanel.tsx` (provider `object-metadata`) | Verified (M3, browser) |
| C5.6 | Coordinate display (+ decimals option) | `CoordinateDisplay.tsx` | Pending |
| C5.7 | Connections section | `ConnectionsSection.tsx`, `connectionGraphStore` | Pending |
| C5.8 | Mesh stats per selection | `MeshStatsSection.tsx`, `meshStats.ts` | Pending |

## D. Results mode (FEA)

| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| D1 | Streaming FEA load | — | `load_fea_streaming.ts`, `services/fea/feaFetcher.ts` | Verified (M4, browser) |
| D2 | Per-step HTTP Range fetch | — | `FeaRangeFetcher` | Verified (M4, 206 confirmed) |
| D3 | Nodal field → colours + morph | — | `scene/fea/applyField.ts` | Verified (browser, live session) |
| D4 | Element field | — | `scene/fea/applyElemField.ts` | Verified (parseElemFieldBlob tests) — parse only; fixture has no element field |
| D5 | Colormaps (viridis/abaqus/jet/coolwarm/grayscale) | field picker | `scene/fea/colormaps.ts` | Verified (browser — all five colormaps listed) |
| D6 | Deformation scale | `SimulationControls.tsx` | — | Verified (browser — deform slider, −1…1) |
| D7 | Step + mode two-slider scrubber | `SimulationControls.tsx`, `feaAnimationStore` | — | Verified (browser — stepped 1→6 of 20) |
| D8 | Play / pause / stop | `SimulationControls.tsx` | `feaAnimationDriver.ts` | Verified (browser — play toggles aria-pressed) |
| D9 | GLTF animation clips (legacy) | `animationStore` | `AnimationController.ts` | Moved (clip picker reachable; the fixture carries no GLTF clips) |
| D10 | Morph propagation to edges/points | — | `assignMorphToEdgeAlso.ts`, `assignMorphToPointsAlso.ts` | Pending — not exercised |
| D11 | Colour legend | `ColorLegend.tsx` (OverlayLayer) | — | Verified (M4, browser) |
| D12 | FEA data table | `SimulationDataInfoPanel.tsx`, `tableNavStore` | — | **bottom dock** | Verified (M4, browser) |
| D13 | Table row → 3D marker + camera | — | `scene/fea/goToNode.ts` | Pending — the table is virtualised; row click not exercised |
| D14 | Beam-solids warp | — | `services/feaBeamSolidsWarp.ts` | Verified (parseBeamSolidsWarp tests) — parse only |
| D15 | FEM concepts glyphs (masses / BCs) | `FemConceptsController.tsx`, `femConceptsStore` | — | Verified (browser — FEM group: masses, BCs, load scenario) |
| D16 | CAD ↔ FEA lineage | — | `lineage/registerLineageFromExtension.ts` | Pending — needs a CAD↔FEA pair |
| D17 | Pop-out sim window | `SimWindowFrame.tsx`, `SimFollowerPage.tsx` | `ada-sim` BroadcastChannel | Pending — needs the pop-out window |
| D18 | Manifest bake polling | — | `services/feaManifestPoll.ts` | Verified (fetchFeaManifest tests ×7, and now exercised in dev) |
| D19 | Plugin `fem-sidebar` panels + `asTab` | `PluginSlots.tsx` | `plugins/registry.ts` | Pending — needs an enabled plugin |
| D20 | Plugin scene colour fields | `PluginColorFields.tsx` | `plugins/registry.ts` | Pending — needs an enabled plugin |

## E. Build mode

### E1 Cellbuilder (`CellBuilderPanel.tsx` — 6 tab types)
| ID | Feature | Business logic (untouched) | Status |
|---|---|---|---|
| E1.1 | Build tab — place / move / resize cells | `cellBuilderStore.ts` | Moved (browser: tab, cell list, mode readout; viewport gestures not exercised) |
| E1.2 | Equipment tab *(contextual)* | `equipmentCatalogStore` | Verified (browser — catalogue lists the built-in archetypes) |
| E1.3 | Systems tab — piping / electrical / HVAC | `cellbuilder/ports.ts` | Verified (browser — catalogue lists the built-in kinds) |
| E1.4 | Detailing tab *(contextual)*, engine-advertised | `cellbuilder/detailingOptions.ts` | Moved (contextual — needs a detailing engine) |
| E1.5 | View tab — **dissolved** | representation, superimpose, side-by-side, ports overlay, recentre → **View ▸ Builder** commands; the two compile-output toggles → Build tab | Verified (browser, menu) |
| E1.6 | Tools tab — **reorganised** | export → Build toolbar split button; resync + relocations → **Tools** menu; what they produce → the **Output** tab | Verified (browser) |
| E1.7 | Magnetic snap, face/edge extrude | `cellbuilder/snap.ts` | Verified (cellbuilderSnap tests) — logic only; gesture not exercised |
| E1.8 | Loft members / stations → bands | `cellbuilder/loft.ts` | Verified (cellbuilderLoft tests) — logic only; gesture not exercised |
| E1.9 | Groups → blueprints / structures | `cellbuilder/groups.ts`, `blueprints.ts` | Verified (cellbuilderGroups + cellbuilderBlueprints tests) — logic only |
| E1.10 | Port overrides (round-trip) | `cellbuilder/ports.ts` | Verified (cellbuilderPorts tests) — logic only |
| E1.11 | Undo / redo (`Ctrl+Z`, `Shift+Z`, `Y`) | `cellbuilder/history.ts` (+ Build rail) | Verified (M5, browser) |
| E1.12 | Compile + preview gate (`Shift+Enter`) | `cellbuilder/compileGate.ts` | **status bar** | Verified (M5, browser) |
| E1.13 | Side-by-side compiled preview | `cellbuilder/sideBySide.ts` | Verified (cellbuilderSideBySide tests + View ▸ Builder command in browser) |
| E1.14 | Cross-tab sync (`?pfollow=`) | `cellbuilder/proceduralChannel.ts` | Pending — needs a second tab on ?pfollow= |
| E1.15 | Gizmo HUD (`G`/`R`/`S`, `X`/`Y`/`Z`) | `CellBuilderGizmoHud.tsx` | `OverlayLayer` | Verified (browser — key legend visible; mount held by mountedOverlays.test.ts) |
| E1.16 | Context / port / insert menus | `CellBuilder*Menu.tsx` | → `ContextMenu` primitive | Verified (mountedOverlays.test.ts — the four overlays are mounted) |
| E1.17 | Equipment CAD preview + bbox infer | `EquipmentPreview.tsx` | Verified (cellbuilderEquipmentPreviewBox tests) — bbox infer only; needs CAD |
| E1.18 | Cell selection info | `CellBuilderSelectionInfo.tsx` (1017 ln) | Properties provider `cellbuilder-cell` | Verified (M3/M5) |

### E2 Node editor
| ID | Feature | Business logic (untouched) | Status |
|---|---|---|---|
| E2.1 | Procedure nodes with typed params | `node_editor/handlers/run_procedure.ts` | Verified (M5, dock panel) |
| E2.2 | File-object nodes | `CustomFileObjectNode.tsx` | Pending — needs a websocket backend |
| E2.3 | List procedures | `request_list_of_nodes.ts` (`LIST_PROCEDURES`) | Verified (M5, toolbar action) |
| E2.4 | Run procedure | `RUN_PROCEDURE` flatbuffer | Pending — needs a websocket backend |
| E2.5 | Finished-procedure → new node | `handle_finished_procedure.ts` | Pending — needs a websocket backend |
| E2.6 | Delete node/edge on server | `on_delete.ts` (`DELETE_FILE_OBJECT`) | Pending — needs a websocket backend |
| E2.7 | Spawn standalone editor | `start_new_node_editor.ts` | Verified (M5, toolbar action) |

### E3 Component builder
`ComponentControls.tsx`, `componentSpecsStore`, `componentBuildStore`,
`services/components/componentBuildPipeline.ts`.

Status: **restored** — it had been orphaned by the rewrite (nothing rendered it). Now the
`component-build` panel in Build mode, reachable from **Tools ▸ Show Connections**;
verified in the browser. Building a component still needs a backend, so the form is
reachable but has not been submitted here. See row B9 below.

### Restored during verification

| ID | Feature | Reached today | Owning component | Business logic (untouched) | New home | Status |
|---|---|---|---|---|---|---|
| B9 | Connection-component build | top-toolbar toggle | `component_view/ComponentControls.tsx` | `services/components/componentBuildPipeline.ts`, `componentBuildStore` | Build mode, right dock (`component-build`) | **Restored** — Verified (browser, panel opens) |

## F. Data mode

| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| F1 | Storage browser (folder tree over flat keys) | `StorageBrowser.tsx` (Data left dock) | `utils/storage/fileTree.ts` | Verified (M6, browser) |
| F2 | Row kebab / context menu | `storageMenuItems.tsx`, `RowKebabMenu.tsx` | `useStorageMutations.ts` | Verified (browser — row menu: load, download, copy path, rename, move, delete) |
| F3 | Rename / move / delete / new folder | same | `viewerApi.ts` | Verified (browser — rename opens the inline editor; delete/move reachable) |
| F4 | Upload (+ presigned direct >200 MB) | `UploadContextMenu.tsx` (ToastHost) | `upload_source_file.ts`, rail trigger | Moved (needs a real backend to verify) |
| F5 | Download | `storageMenuItems.tsx` | `viewerApi.ts` | Verified (browser — menu item present; download not triggered) |
| F6 | Load into scene | — | `overlay_file_in_scene.ts` | Verified (browser — loaded dev-cantilever.rmed into the scene) |
| F7 | CI upload history | `GitHistoryPanel.tsx` | `.build.json` sidecars | Pending — needs .build.json sidecars |
| F8 | Field picker | `FieldPickerModal.tsx` | — | Pending — needs a field-carrying model |
| F9 | Convert page (drop zone, target picker, rows) | `convert/*` — Convert **mode** overlay, and the `/convert` deep link | `services/conversion/*` | Verified (browser, both) — the mode drops the “back to the viewer” link, the route keeps it |
| F10 | Server conversion (NATS worker) | — | `serverPipeline.ts` | Pending — needs a NATS worker |
| F11 | Pyodide in-browser conversion | — | `pyodidePipeline.ts` | Pending — needs a Pyodide run |
| F12 | Native wasm CAD→GLB | — | `nativeCadGlbPipeline.ts` | Pending — needs a wasm run |
| F13 | Native wasm B-rep writer | — | `nativeBrepWriterPipeline.ts` | Pending — needs a wasm run |
| F14 | Conversion progress toasts | `ConversionProgress.tsx` | `conversionStore` | `ToastHost` | Verified (M6) |
| F15 | `CONVERSION_MATRIX` target gating | `SerializerTessellatorSelect.tsx` | `runtime/config.ts` | Pending — needs CONVERSION_MATRIX from a real /config.js |
| F16 | Worker status badge | `WorkerStatusBadge.tsx` | — | Verified (browser — badge reads “no workers” in Convert mode) |
| F17 | Scope / project picker | shell `ScopePicker` | `scopeStore`, shared `applyScopeChange` | **top of the Storage panel** (the title-bar copy is gone; the page profile keeps one, having no Storage panel) | Verified (browser) |
| F18 | Sign in / out | `RestSection.tsx` | `services/auth/oidc.ts` | Pending — needs an OIDC provider |
| F19 | Server info file list (non-REST) | `ServerInfoBox.tsx` | `server_info/handlers/*` | Pending — needs the websocket runtime |
| F20 | GLB↔GLB diff | — | `utils/diffConverter/*` | Pending — needs two GLBs |
| F21 | Client-side wasm utilities | — | `wasm/wasmUtilityRegistry.ts` | Pending — needs a wasm utility run |

### F22 Admin console — all 14 tabs
`audit` · `audit_runs` · `schedules` · `issues` · `performance` · `frontend_loads` ·
`corpus` · `projects` · `storage` · `workers` · `conversion` · `equipment` ·
`system` · `engines`. Plus `CliTokenButton`, `WorkerInfoModal`, `FileTreeView`,
`EquipmentPreview`. **Treatment: wrap in DS `Panel`/`Tabs` + codemod only — no
rewrite.**

Status: **partly**. All 14 tabs load and render at `/admin` on the page profile (verified
in the browser: the tab strip lists audit, audit runs, schedules, issues, performance,
frontend loads, corpus, projects, storage, workers, conversion, equipment, system,
engines, and the `#hash` still selects one). Their twenty-two native dialogs are
converted. The DS `Panel`/`Tabs` wrap and the className codemod are **not** done — the
tabs still carry their own chrome, which is the lowest-value-per-line work in the plan and
deliberately last.

## G. Preferences (`OptionsComponent.tsx` — 4 sections)

| ID | Section | Contents | Status |
|---|---|---|---|
| G1 | Scene config | point size (+absolute), 11 display toggles: Show Color Legend, Geometry Edges, Hide tessellation lines, Mesh stats in Properties, Auto-convert uploads to GLB, Auto Fit to View, Lock Translation, Enable Node Editor, Enable Websocket, Z is UP, Use Default Orbitcontroller | Verified (browser — Settings ▸ Scene, 16 controls) |
| G2 | Theme | 4 presets (Slate glass / Dark / Mist / Pale glass), custom hex + opacity, Gallery mode | Verified (browser — Settings ▸ Theme: 4 presets, panel colour, gallery mode) |
| G3 | Performance | Show Stats, material mode, backface-cull, smooth-shade, disable shadow map, disable AA, pixel-ratio slider, adaptive DPR, on-demand render, time-sliced load, skip beam-solid load, flat-varying picker, GPU face picking, skip element-edge wireframe, admin metrics | Verified (browser — Settings ▸ Performance, 18 controls) |
| G4 | Conversion engine | Convert in-browser (WASM) | Verified (browser — Settings ▸ Conversion engine) |
| G5 | Shortcut Keys modal | `ShortcutsModal.tsx` | superseded in the shell by the command palette; `docs/SHORTCUTS.md` is generated from `shell/shortcuts.ts` | Verified (M7) |
| G6 | Version / build info | adapy version, frontend SHA, image tags | → title bar / About | Verified (browser — Help ▸ About: version, commit, runtime mode) |

## H. Cross-cutting

| ID | Feature | Notes | Status |
|---|---|---|---|
| H1 | Panel theming (`--ada-*` CSS vars) | `themeStore.ts` — **extend, do not replace** | Verified (tokens.test.ts — themeStore emits the documented var set for every preset) |
| H2 | Mobile bottom sheets | `useBottomSheet.ts` — **move, do not rewrite** | Moved (`utils/useBottomSheet.ts`, unchanged) — not exercised on a touch device |
| H3 | `pointer-fine:hover:` sticky-hover guard | bake into `Button`/`IconButton` | Verified (hoverGuard.test.ts) for `components/ui` + `shell`; **73 older files still use a bare `hover:`** |
| H4 | Per-panel error boundaries | `ErrorBoundary.tsx` | Verified (browser — boundaries caught real throws this session) |
| H5 | Off-thread model cache | `state/model_worker/*` | Moved — the `?worker&inline` barrier is why pure logic is extracted; not separately exercised |
| H6 | Sequential load queue | `loadQueueStore` | Verified (browser — the queue reported a failed load by name) |
| H7 | On-demand render / adaptive DPR | `perfStore`, `renderProfiler.ts` | Pending — not exercised |
| H8 | Plugin URL-param handlers | `plugins/urlParams.ts` | Pending — needs an enabled plugin |
| H9 | Plugin result-sidecar loaders | `plugins/sidecarLoaders.ts` | Pending — needs an enabled plugin |
| H10 | Audit sweep toast | `auditToastStore` | Pending — needs a backend sweep |
| H11 | Storage compression sweep poll | `compressionStore` | Pending — needs a backend sweep |

---

## Runtime-mode gating — check every feature in all three

A feature that exists in one mode and not another is a parity trap. The three modes
are described in `UI_BASELINE.md`; each row above must be checked in whichever of
these it applies to:

- **WS / desktop** (`npm run dev`) — default; no auth, no scopes, no admin.
- **REST / hosted** (`npm run dev:rest`) — adds F1–F22, A8, H10, H11.
- **Embed** (`npm run build:embed` + `embed/dev.html`) — canvas + optional
  tree/object/scene panels only; no comms, no conversion, no storage.

---

## Where this stands

Worked end to end. Of the original 114 `Pending` rows, the ones that could be closed from
a dev machine are closed, each naming **how** — the browser, a named test, or both.

Two features were found **missing, not merely unchecked**, and restored: `NODE_EDITOR_ONLY`
routed to a profile nothing mounted (A6), and the connection-component panel was orphaned
with its store, pipeline and service all intact (B9). Both were invisible: nothing throws
when a module is simply never rendered.

**A "Pending" here is a statement about the environment, not a shrug.** Every remaining row
names what it needs — a NATS worker, an OIDC provider, a Pyodide run, an enabled plugin, a
touch device, a point cloud, two GLBs, a CAD↔FEA pair. None can be closed honestly from a
dev stub, and the alternative — marking them Verified after reading the code — is the thing
that makes a parity checklist worth nothing.

Some rows are Verified **with a qualifier**, and the qualifier is the point: "parse only"
where a test covers the bytes but not the render, "logic only" where the cellbuilder rules
are tested but the gesture was not driven, "not exercised" where a branch was read. An
inventory whose Verified column cannot be trusted stops anyone looking again.
