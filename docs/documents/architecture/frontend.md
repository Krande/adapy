# Frontend

`src/frontend` is the viewer: a React 19 + three.js single-page app built with Vite. The same
code ships in four forms:

| Form | Build | Where it runs |
|---|---|---|
| Hosted SPA | `npm run build:serve` (inside `Dockerfile.viewer`) | Served by the REST API, talks to `/api/*` |
| Local viewer | `npm run build` → `embed-script.cjs` inlines JS + CSS into one HTML → `ada/visit/rendering/resources/index.zip` | `obj.show()` from any Python script: served by `ada.comms.web.serve` and fed by the local `wsock` server, in a browser tab |
| Inline viewer | the same `index.zip` bundle | `obj.show()` in Jupyter: an `<iframe srcdoc>` in the cell output with the GLB inlined (`window.B64GLTF`) |
| Embeddable viewer | `vite.config.embed.ts` | `mountViewer(element, {modelBytes, camera, showControls})` → `{dispose()}`; several per page (`embed/viewerInstances.ts`), used by the paradoc FEA report |

## Structure

```mermaid
flowchart TB
    subgraph ui["UI"]
        COMP["components/<br/>viewer · tree_view · asset_browser · info_box_scene<br/>simulation · admin · options · auth"]
        SHELL["plugins/uiShells.ts · UiShellHost.tsx<br/>alternative UI shells"]
    end
    subgraph state["State (zustand, ~50 stores)"]
        ST["state/*Store.ts<br/>model · selection · tree · assets · clash · groups · admin …"]
        MW["state/model_worker/<br/>modelCache.worker.ts (Dexie/IndexedDB via Comlink)"]
    end
    subgraph services["Services"]
        API["services/api/*.ts<br/>typed REST client"]
        TR["services/transport.ts<br/>REST ↔ WebSocket"]
        WS["utils/comms/ws_comms.ts · rest_comms.ts"]
        FB["flatbuffers/ (generated)"]
    end
    subgraph scene["Scene (three.js)"]
        CORE["viewer-core/<br/>stable surface for shells and plugins"]
        LOAD["components/viewer/sceneHelpers/setupModelLoader"]
        HANDLERS["utils/scene/handlers/<br/>view_file_object_from_server · upload_source_file<br/>overlay_file_in_scene · side_by_side · load_fea_streaming"]
        FEA["utils/scene/fea/streaming/<br/>session · manifestStores · paintNodeField<br/>paintElemField · warp · beamSolids (worker)"]
        PICK["utils/mesh_select/<br/>CustomBatchedMesh · GpuMeshPicker · edge workers"]
    end
    subgraph conv["In-browser conversion"]
        PYO["utils/pyodide/pyodide_worker.js<br/>Pyodide + ifcopenshell wasm + trimesh<br/>+ adacpp wasm wheel"]
        NAT["utils/nativeConvert/<br/>emscripten workers"]
    end
    PLUG["plugins/registry.ts<br/>registerPlugin (coreApiRange semver gate)<br/>packages/plugins/*"]

    COMP --> ST
    COMP --> CORE
    SHELL --> CORE
    PLUG --> CORE
    ST --> API & TR
    TR --> WS --> FB
    HANDLERS --> LOAD --> CORE
    HANDLERS --> FEA
    ST --> MW
    LOAD --> PICK
    HANDLERS --> PYO & NAT

    click COMP href "https://github.com/Krande/adapy/tree/main/src/frontend/src/components" "React components"
    click SHELL href "https://github.com/Krande/adapy/blob/main/src/frontend/src/plugins/uiShells.ts" "UI shells"
    click ST href "https://github.com/Krande/adapy/tree/main/src/frontend/src/state" "zustand stores"
    click MW href "https://github.com/Krande/adapy/blob/main/src/frontend/src/state/model_worker/modelCache.worker.ts" "IndexedDB model cache worker"
    click API href "https://github.com/Krande/adapy/tree/main/src/frontend/src/services/api" "Typed REST client"
    click TR href "https://github.com/Krande/adapy/blob/main/src/frontend/src/services/transport.ts" "REST / WebSocket transport switch"
    click WS href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/comms" "WebSocket and REST comms"
    click FB href "https://github.com/Krande/adapy/tree/main/src/frontend/src/flatbuffers" "Generated FlatBuffers bindings"
    click CORE href "https://github.com/Krande/adapy/tree/main/src/frontend/src/viewer-core" "Stable viewer API"
    click LOAD href "https://github.com/Krande/adapy/tree/main/src/frontend/src/components/viewer/sceneHelpers" "Model loading"
    click HANDLERS href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/scene/handlers" "Scene handlers"
    click FEA href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/scene/fea/streaming" "FEA streaming"
    click PICK href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/mesh_select" "Batched meshes and GPU picking"
    click PYO href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/pyodide" "Pyodide conversion worker"
    click NAT href "https://github.com/Krande/adapy/tree/main/src/frontend/src/utils/nativeConvert" "Emscripten conversion workers"
    click PLUG href "https://github.com/Krande/adapy/blob/main/src/frontend/src/plugins/registry.ts" "Plugin registry"
```

Main libraries: three.js, camera-controls, zustand, Comlink, Dexie, flatbuffers,
meshoptimizer, react-arborist (trees) and `@xyflow/react` (node editor). Styling uses
Tailwind 4. Plugins live in npm workspaces under `packages/plugins/*`, and
`npm run gen:plugins` generates `registry.generated.ts`.

## Loading a model

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant H as scene handler
    participant A as REST API
    participant S as Object storage
    participant L as setupModelLoader
    participant C as model cache worker (IndexedDB)
    participant V as three.js scene

    U->>H: open source (tree / asset browser / upload)
    H->>A: request derived GLB (convert job if missing)
    A-->>H: presigned URL (after the job, if one was needed)
    H->>S: GET GLB
    H->>L: GLB bytes
    L->>L: parse glTF + ADA_EXT_data
    L->>C: cache hierarchy · draw ranges · face ranges
    L->>V: batched meshes + picking ranges
```

`ADA_EXT_data` provides the object hierarchy and per-object draw ranges, so one merged mesh
can still be picked, hidden and coloured object by object (`utils/mesh_select`).

## Loading FEA results

```mermaid
sequenceDiagram
    autonumber
    participant H as load_fea_streaming.ts
    participant A as REST /fea/*
    participant S as Object storage
    participant F as fea/streaming/*
    H->>A: fea/manifest (poll until the bake job is done)
    A-->>H: fea.manifest.json
    H->>S: fea.mesh.glb (+ edges, elements, beam solids)
    H->>F: build session: mesh + manifest stores
    loop on step / field change
        F->>S: range GET of one step from fea.FIELD.bin
        F->>F: paintNodeField / paintElemField (fixed colour range from the manifest)
        F->>F: warp (deformed shape) · beamSolids worker
    end
```

Because each field blob is step-major with a fixed header, one step is one HTTP range
request. Colour ranges come pre-computed from the manifest, so the legend stays fixed while
scrubbing through steps.

A `transient` result (a time history, for example an OpenCourant `.radanim`) opens on its last
frame. The step slider becomes a timeline, and Play steps through it at true time scale
(`utils/scene/fea/timeHistory.ts`). Step loads run one at a time, and the latest request wins.
Any result can be exported as a video (`utils/scene/fea/animationExport/`): MP4 through
WebCodecs and mediabunny, or GIF through gifenc. You choose resolution, aspect, frame rate, and
whether to include the legend and orientation gizmo. The encoders load only when used.

## In-browser conversion

Without a worker (or for local files), `pyodide_converter.ts` lazily starts
`pyodide_worker.js`: Pyodide with ifcopenshell (wasm) and trimesh for IFC → GLB, and the
adacpp wasm wheel for STEP → GLB. The worker is recycled above ~1.4 GB of heap. The browser
can also run the FEA bake and upload the artefacts through the REST API.
