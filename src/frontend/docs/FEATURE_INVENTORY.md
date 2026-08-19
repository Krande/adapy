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
| A1 | Main viewer app | `/` | `app.tsx` → `AppBody` | — | `AppShell profile="viewer"` | Pending |
| A2 | OIDC redirect landing | `/auth/callback` | `auth/AuthCallback.tsx` | `services/auth/oidc.ts` | unchanged, outside the shell | Pending |
| A3 | Convert page | `/convert` | `convert/ConvertPage.tsx` | `services/conversion/*` | Data mode workspace + `profile="page"` deep link | Pending |
| A4 | Admin console | `/admin` (+ `#tab`) | `admin/AdminPanel.tsx` | `services/viewerApi.ts` | Data mode workspace + `profile="page"` deep link | Pending |
| A5 | Simulation follower window | `?simfollow=` | `simulation/SimFollowerPage.tsx` | `ada-sim` BroadcastChannel | `profile="window"` | Pending |
| A6 | Node-editor-only window | `NODE_EDITOR_ONLY` | `node_editor/NodeEditorComponent.tsx` | flatbuffers procedures | `profile="graph"` | Pending |
| A7 | Paradoc / Jupyter embed | `mountViewer()` | `embed/EmbedUI.tsx` | `embed/index.ts` | `profile="embed"` (EmbedUI deleted) | Pending |
| A8 | Auth gate (REST) | wraps app | `auth/AuthGate.tsx` | `services/auth/oidc.ts`, `meStore` | unchanged, wraps `AppShell` | Pending |
| A9 | Fatal error boundary | on throw | `common/ErrorBoundary.tsx` | — | unchanged, plus per-dock boundaries | Pending |

## B. Top toolbar (`components/Menu.tsx`) — every button

| ID | Button / title | Store flag | New home | Status |
|---|---|---|---|---|
| B1 | ☰ Toggle options drawer | `optionsStore.isOptionsVisible` | Preferences panel + title-bar menu set | Pending |
| B2 | Show/Hide selection tree (`Shift+T`) | `treeViewStore.isTreeCollapsed` | Outliner, left dock | Pending |
| B3 | Toggle node editor | `useNodeEditorStore.isNodeEditorVisible` | Build mode, dock panel | Pending |
| B4 | Storage (REST) | `serverInfoStore.showServerInfoBox` | Data mode, left dock | Pending |
| B5 | Toggle object info | `objectInfoStore.show_info_box` | **Properties panel, right dock** (unified) | Pending |
| B6 | Toggle scene info | `sceneInfoStore.show_scene_info_box` | Scene panel, right dock | Pending |
| B7 | Reload nodes (graph mode) | — | `profile="graph"` toolbar | Pending |
| B8 | Toggle animation controls | `animationStore.isControlsVisible` | Results mode, right dock | Pending |
| B9 | Toggle connection-component panel | `componentControlsStore.isVisible` | Build mode, right dock | Pending |
| B10 | Toggle procedural cellbuilder panel | `cellBuilderStore.panelVisible` | Build mode, right dock | Pending |
| B11 | Plugin top-bar buttons | `pluginUiStore` visible-map | preserved via `PluginRegion` compat map | Pending |
| B12 | Websocket status | `websocketStatusStore.showInfoBox` | **Status bar** | Pending |

## C. Inspect mode

### C1 Selection and navigation
| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| C1.1 | Click-to-select mesh (GPU pick) | `setupPointerHandler.ts` | `mesh_select/GpuMeshPicker.ts`, `CustomBatchedMesh.ts` | Pending |
| C1.2 | Face-level picking | Scene ▸ Tools | `mesh_select/faceHighlight.ts`, `GpuMeshPicker` | Pending |
| C1.3 | Point-cloud picking | option `useGpuPointPicking` | `mesh_select/GpuPointPicker.ts` | Pending |
| C1.4 | Selection highlight | — | `utils/default_materials.ts` `selectedMaterial` | Pending |
| C1.5 | Model hierarchy tree | `tree_view/ResizableTreeView.tsx` | `utils/tree_view/treeGraph.ts`, `react-arborist` | Pending |
| C1.6 | Tree keyboard nav (`Shift+↑↓←→`) | `setupCameraControlsHandlers.ts:126-140` | `utils/tree_view/treeNavigation.ts` | Pending |
| C1.7 | Cross-model select | — | `utils/scene/crossModelSelect.ts` | Pending |
| C1.8 | Copy selected names (`Shift+C`) | — | `utils/clipboard/copySelectionNames.ts` | Pending |
| C1.9 | Double-click recentre pivot | `setupPointerHandler.ts` | same | Pending |

### C2 Visibility and camera
| ID | Feature | Reached today | Business logic | Status |
|---|---|---|---|---|
| C2.1 | Hide selection (`Shift+H`) | shortcut + Object Info | `utils/scene/visibility.ts` | Pending |
| C2.2 | Unhide all (`Shift+U`) | shortcut + Object Info | same | Pending |
| C2.3 | Centre on selection (`Shift+F`) | shortcut | `centerViewOnSelection.ts` | Pending |
| C2.4 | Zoom to fit (`Shift+A`) | shortcut | `setupCameraControlsHandlers.ts` `zoomToAll` | Pending |
| C2.5 | Orbit / pan / zoom | canvas | OrbitControls or `camera-controls` | Pending |
| C2.6 | Orientation gizmo (view cube) | canvas corner | `addOrientationGizmo` | Pending |
| C2.7 | Adaptive near/far clipping | automatic | `applyAdaptiveClipping` | Pending |
| C2.8 | Grid helper, Z-up toggle | Options | `addDynamicGridHelper` | Pending |

### C3 Scene panel tabs (`SceneInfoBox.tsx` — 6 tabs, 2 contextual)
| ID | Tab ▸ section | Owning component | Status |
|---|---|---|---|
| C3.1 | Model ▸ loaded models / overlay / unload | `LoadedModelsSection.tsx` | Pending |
| C3.2 | Model ▸ source + re-convert | `SourceSection.tsx` | Pending |
| C3.3 | Model ▸ stats | `StatsSection.tsx`, `ModelStatsSection.tsx` | Pending |
| C3.4 | Model ▸ groups | `GroupsSection.tsx` | Pending |
| C3.5 | Tools ▸ utilities | `UtilitiesSection.tsx` | Pending |
| C3.6 | Tools ▸ face picking toggle | `FacePickingToggle.tsx` | Pending |
| C3.7 | Tools ▸ face search | `FaceSearchSection.tsx` | Pending |
| C3.8 | Clip ▸ section planes | `SectionPlanesPanel.tsx` + `SectionPlanesController.tsx` | Pending |
| C3.9 | Mesh ▸ distortion scan | `MeshDistortionSection.tsx`, `meshPanelStore` | Pending |
| C3.10 | FEM ▸ concepts *(contextual)* | `FemConceptsPanel.tsx` | Pending |
| C3.11 | Joints ▸ overview *(contextual)* | `JointsOverviewPanel.tsx` | Pending |

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
| C5.5 | Object metadata | `ObjectMetadataPanel.tsx` | Pending |
| C5.6 | Coordinate display (+ decimals option) | `CoordinateDisplay.tsx` | Pending |
| C5.7 | Connections section | `ConnectionsSection.tsx`, `connectionGraphStore` | Pending |
| C5.8 | Mesh stats per selection | `MeshStatsSection.tsx`, `meshStats.ts` | Pending |

## D. Results mode (FEA)

| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| D1 | Streaming FEA load | — | `load_fea_streaming.ts`, `services/fea/feaFetcher.ts` | Pending |
| D2 | Per-step HTTP Range fetch | — | `FeaRangeFetcher` | Pending |
| D3 | Nodal field → colours + morph | — | `scene/fea/applyField.ts` | Pending |
| D4 | Element field | — | `scene/fea/applyElemField.ts` | Pending |
| D5 | Colormaps (viridis/abaqus/jet/coolwarm/grayscale) | field picker | `scene/fea/colormaps.ts` | Pending |
| D6 | Deformation scale | `SimulationControls.tsx` | — | Pending |
| D7 | Step + mode two-slider scrubber | `SimulationControls.tsx`, `feaAnimationStore` | — | Pending |
| D8 | Play / pause / stop | `SimulationControls.tsx` | `feaAnimationDriver.ts` | Pending |
| D9 | GLTF animation clips (legacy) | `animationStore` | `AnimationController.ts` | Pending |
| D10 | Morph propagation to edges/points | — | `assignMorphToEdgeAlso.ts`, `assignMorphToPointsAlso.ts` | Pending |
| D11 | Colour legend | `ColorLegend.tsx`, `colorLegendStore` | — | Pending |
| D12 | FEA data table | `SimulationDataInfoPanel.tsx`, `tableNavStore` | — | **bottom dock** | Pending |
| D13 | Table row → 3D marker + camera | — | `scene/fea/goToNode.ts` | Pending |
| D14 | Beam-solids warp | — | `services/feaBeamSolidsWarp.ts` | Pending |
| D15 | FEM concepts glyphs (masses / BCs) | `FemConceptsController.tsx`, `femConceptsStore` | — | Pending |
| D16 | CAD ↔ FEA lineage | — | `lineage/registerLineageFromExtension.ts` | Pending |
| D17 | Pop-out sim window | `SimWindowFrame.tsx`, `SimFollowerPage.tsx` | `ada-sim` BroadcastChannel | Pending |
| D18 | Manifest bake polling | — | `services/feaManifestPoll.ts` | Pending |
| D19 | Plugin `fem-sidebar` panels + `asTab` | `PluginSlots.tsx` | `plugins/registry.ts` | Pending |
| D20 | Plugin scene colour fields | `PluginColorFields.tsx` | `plugins/registry.ts` | Pending |

## E. Build mode

### E1 Cellbuilder (`CellBuilderPanel.tsx` — 6 tab types)
| ID | Feature | Business logic (untouched) | Status |
|---|---|---|---|
| E1.1 | Build tab — place / move / resize cells | `cellBuilderStore.ts` | Pending |
| E1.2 | Equipment tab *(contextual)* | `equipmentCatalogStore` | Pending |
| E1.3 | Systems tab — piping / electrical / HVAC | `cellbuilder/ports.ts` | Pending |
| E1.4 | Detailing tab *(contextual)*, engine-advertised | `cellbuilder/detailingOptions.ts` | Pending |
| E1.5 | View tab | — | Pending |
| E1.6 | Tools tab | — | Pending |
| E1.7 | Magnetic snap, face/edge extrude | `cellbuilder/snap.ts` | Pending |
| E1.8 | Loft members / stations → bands | `cellbuilder/loft.ts` | Pending |
| E1.9 | Groups → blueprints / structures | `cellbuilder/groups.ts`, `blueprints.ts` | Pending |
| E1.10 | Port overrides (round-trip) | `cellbuilder/ports.ts` | Pending |
| E1.11 | Undo / redo (`Ctrl+Z`, `Shift+Z`, `Y`) | `cellbuilder/history.ts` | Pending |
| E1.12 | Compile + preview gate (`Shift+Enter`) | `cellbuilder/compileGate.ts` | **status bar** | Pending |
| E1.13 | Side-by-side compiled preview | `cellbuilder/sideBySide.ts` | Pending |
| E1.14 | Cross-tab sync (`?pfollow=`) | `cellbuilder/proceduralChannel.ts` | Pending |
| E1.15 | Gizmo HUD (`G`/`R`/`S`, `X`/`Y`/`Z`) | `CellBuilderGizmoHud.tsx` | `OverlayLayer` | Pending |
| E1.16 | Context / port / insert menus | `CellBuilder*Menu.tsx` | → `ContextMenu` primitive | Pending |
| E1.17 | Equipment CAD preview + bbox infer | `EquipmentPreview.tsx` | Pending |
| E1.18 | Cell selection info | `CellBuilderSelectionInfo.tsx` (1017 ln) | → Properties provider, moved verbatim | Pending |

### E2 Node editor
| ID | Feature | Business logic (untouched) | Status |
|---|---|---|---|
| E2.1 | Procedure nodes with typed params | `node_editor/handlers/run_procedure.ts` | Pending |
| E2.2 | File-object nodes | `CustomFileObjectNode.tsx` | Pending |
| E2.3 | List procedures | `request_list_of_nodes.ts` (`LIST_PROCEDURES`) | Pending |
| E2.4 | Run procedure | `RUN_PROCEDURE` flatbuffer | Pending |
| E2.5 | Finished-procedure → new node | `handle_finished_procedure.ts` | Pending |
| E2.6 | Delete node/edge on server | `on_delete.ts` (`DELETE_FILE_OBJECT`) | Pending |
| E2.7 | Spawn standalone editor | `start_new_node_editor.ts` | Pending |

### E3 Component builder
`ComponentControls.tsx`, `componentSpecsStore`, `componentBuildStore`,
`services/components/componentBuildPipeline.ts`. Status: Pending.

## F. Data mode

| ID | Feature | Owning component | Business logic (untouched) | Status |
|---|---|---|---|---|
| F1 | Storage browser (folder tree over flat keys) | `StorageBrowser.tsx` | `utils/storage/fileTree.ts` | Pending |
| F2 | Row kebab / context menu | `storageMenuItems.tsx`, `RowKebabMenu.tsx` | `useStorageMutations.ts` | Pending |
| F3 | Rename / move / delete / new folder | same | `viewerApi.ts` | Pending |
| F4 | Upload (+ presigned direct >200 MB) | `UploadContextMenu.tsx` | `upload_source_file.ts` | Pending |
| F5 | Download | `storageMenuItems.tsx` | `viewerApi.ts` | Pending |
| F6 | Load into scene | — | `overlay_file_in_scene.ts` | Pending |
| F7 | CI upload history | `GitHistoryPanel.tsx` | `.build.json` sidecars | Pending |
| F8 | Field picker | `FieldPickerModal.tsx` | — | Pending |
| F9 | Convert page (drop zone, target picker, rows) | `convert/*` | `services/conversion/*` | Pending |
| F10 | Server conversion (NATS worker) | — | `serverPipeline.ts` | Pending |
| F11 | Pyodide in-browser conversion | — | `pyodidePipeline.ts` | Pending |
| F12 | Native wasm CAD→GLB | — | `nativeCadGlbPipeline.ts` | Pending |
| F13 | Native wasm B-rep writer | — | `nativeBrepWriterPipeline.ts` | Pending |
| F14 | Conversion progress toasts | `ConversionProgress.tsx` | `conversionStore` | → `ToastHost` | Pending |
| F15 | `CONVERSION_MATRIX` target gating | `SerializerTessellatorSelect.tsx` | `runtime/config.ts` | Pending |
| F16 | Worker status badge | `WorkerStatusBadge.tsx` | — | Pending |
| F17 | Scope / project picker | `RestSection.tsx` | `scopeStore` | → title bar | Pending |
| F18 | Sign in / out | `RestSection.tsx` | `services/auth/oidc.ts` | Pending |
| F19 | Server info file list (non-REST) | `ServerInfoBox.tsx` | `server_info/handlers/*` | Pending |
| F20 | GLB↔GLB diff | — | `utils/diffConverter/*` | Pending |
| F21 | Client-side wasm utilities | — | `wasm/wasmUtilityRegistry.ts` | Pending |

### F22 Admin console — all 14 tabs
`audit` · `audit_runs` · `schedules` · `issues` · `performance` · `frontend_loads` ·
`corpus` · `projects` · `storage` · `workers` · `conversion` · `equipment` ·
`system` · `engines`. Plus `CliTokenButton`, `WorkerInfoModal`, `FileTreeView`,
`EquipmentPreview`. **Treatment: wrap in DS `Panel`/`Tabs` + codemod only — no
rewrite.** Status: Pending.

## G. Preferences (`OptionsComponent.tsx` — 4 sections)

| ID | Section | Contents | Status |
|---|---|---|---|
| G1 | Scene config | point size (+absolute), 11 display toggles: Show Color Legend, Geometry Edges, Hide tessellation lines, Mesh stats in Properties, Auto-convert uploads to GLB, Auto Fit to View, Lock Translation, Enable Node Editor, Enable Websocket, Z is UP, Use Default Orbitcontroller | Pending |
| G2 | Theme | 4 presets (Slate glass / Dark / Mist / Pale glass), custom hex + opacity, Gallery mode | Pending |
| G3 | Performance | Show Stats, material mode, backface-cull, smooth-shade, disable shadow map, disable AA, pixel-ratio slider, adaptive DPR, on-demand render, time-sliced load, skip beam-solid load, flat-varying picker, GPU face picking, skip element-edge wireframe, admin metrics | Pending |
| G4 | Conversion engine | Convert in-browser (WASM) | Pending |
| G5 | Shortcut Keys modal | `ShortcutsModal.tsx` | → regenerated from `shortcuts.ts` | Pending |
| G6 | Version / build info | adapy version, frontend SHA, image tags | → title bar / About | Pending |

## H. Cross-cutting

| ID | Feature | Notes | Status |
|---|---|---|---|
| H1 | Panel theming (`--ada-*` CSS vars) | `themeStore.ts` — **extend, do not replace** | Pending |
| H2 | Mobile bottom sheets | `useBottomSheet.ts` — **move, do not rewrite** | Pending |
| H3 | `pointer-fine:hover:` sticky-hover guard | bake into `Button`/`IconButton` | Pending |
| H4 | Per-panel error boundaries | `ErrorBoundary.tsx` | Pending |
| H5 | Off-thread model cache | `state/model_worker/*` | Pending |
| H6 | Sequential load queue | `loadQueueStore` | Pending |
| H7 | On-demand render / adaptive DPR | `perfStore`, `renderProfiler.ts` | Pending |
| H8 | Plugin URL-param handlers | `plugins/urlParams.ts` | Pending |
| H9 | Plugin result-sidecar loaders | `plugins/sidecarLoaders.ts` | Pending |
| H10 | Audit sweep toast | `auditToastStore` | Pending |
| H11 | Storage compression sweep poll | `compressionStore` | Pending |

---

## Runtime-mode gating — check every feature in all three

A feature that exists in one mode and not another is a parity trap. The three modes
are described in `UI_BASELINE.md`; each row above must be checked in whichever of
these it applies to:

- **WS / desktop** (`npm run dev`) — default; no auth, no scopes, no admin.
- **REST / hosted** (`npm run dev:rest`) — adds F1–F22, A8, H10, H11.
- **Embed** (`npm run build:embed` + `embed/dev.html`) — canvas + optional
  tree/object/scene panels only; no comms, no conversion, no storage.
