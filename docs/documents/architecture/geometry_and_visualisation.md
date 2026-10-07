# Geometry & visualisation

adapy separates *describing* geometry from *building* it:

- **`ada.geom`** is a kernel-free, IFC-like vocabulary of solids, curves and surfaces. Every
  physical object can produce it without a CAD kernel.
- **`ada.cad`** is the boundary to a CAD kernel. It defines the `CadBackend` protocol and
  selects an implementation at runtime: **adacpp** (native C++ on OpenCascade, preferred) or
  **pythonocc** (`ada.occ`).
- **`ada.visit`** turns models, FE meshes and FE results into glTF/GLB scenes for the viewers.

## Layers

```mermaid
flowchart TB
    subgraph objects["Physical objects (ada.api)"]
        OBJ["Beam · Plate · Shape · Pipe · Wall"]
    end

    subgraph geom["ada.geom (kernel-free)"]
        G["Geometry(id, geometry, color)"]
        SOL["solids.py<br/>ExtrudedAreaSolid · RevolvedAreaSolid<br/>FixedReferenceSweptAreaSolid · SweptDiskSolid<br/>Box · Cylinder · AdvancedBrep …"]
        CUR["curves.py<br/>Line · ArcLine · IndexedPolyCurve<br/>BSplineCurveWithKnots …"]
        SUR["surfaces.py<br/>profiles · AdvancedFace · ClosedShell …"]
        BOOL["booleans.py · placement.py"]
        G --- SOL & CUR & SUR & BOOL
    end

    subgraph cad["ada.cad (backend boundary)"]
        PROTO["CadBackend protocol<br/>build · primitives · booleans · tessellate<br/>tessellate_batch · STEP I/O · topology verbs"]
        SEL["select_backend()<br/>prefer arg → ADAPY_CAD_BACKEND → adacpp → pythonocc"]
        BM["BatchMesh<br/>positions f32 · indices u32 · normals<br/>groups: MeshGroup(node_id, start, length, …)"]
        CACHE["shape_cache.get_solid_occ()"]
        DOC["doc.py · DocBackend<br/>(OCAF/XCAF documents)"]
    end

    subgraph backends["Backends"]
        ACPP["AdacppBackend<br/>adacpp (C++ / OCCT)"]
        OCC["ada.occ · OccBackend<br/>pythonocc · geom_to_occ_geom · OCCStore"]
    end

    NGEOM["ada.cadit.ngeom<br/>binary geometry interchange<br/>ada.geom → adacpp"]

    OBJ -- "solid_geom()" --> G
    OBJ -- "solid_occ()" --> CACHE --> PROTO
    PROTO --> SEL
    SEL --> ACPP
    SEL --> OCC
    G -- "geom_to_occ_geom" --> OCC
    G -- "serialize_geometries" --> NGEOM --> ACPP
    PROTO -- "tessellate_batch" --> BM

    click OBJ href "core_model.html#geometry-on-the-object" "solid_geom() / solid_occ() on every object"
    click G href "https://github.com/Krande/adapy/blob/main/src/ada/geom/core.py" "Geometry"
    click SOL href "https://github.com/Krande/adapy/blob/main/src/ada/geom/solids.py" "Solid definitions"
    click CUR href "https://github.com/Krande/adapy/blob/main/src/ada/geom/curves.py" "Curve definitions"
    click SUR href "https://github.com/Krande/adapy/blob/main/src/ada/geom/surfaces.py" "Surface definitions"
    click BOOL href "https://github.com/Krande/adapy/blob/main/src/ada/geom/booleans.py" "BooleanOperation"
    click PROTO href "https://github.com/Krande/adapy/blob/main/src/ada/cad/__init__.py" "CadBackend protocol"
    click SEL href "https://github.com/Krande/adapy/blob/main/src/ada/cad/__init__.py" "select_backend()"
    click BM href "https://github.com/Krande/adapy/blob/main/src/ada/cad/__init__.py" "BatchMesh / MeshGroup"
    click CACHE href "https://github.com/Krande/adapy/blob/main/src/ada/cad/shape_cache.py" "Shape cache"
    click DOC href "https://github.com/Krande/adapy/blob/main/src/ada/cad/doc.py" "OCAF/XCAF document backends"
    click ACPP href "https://github.com/Krande/adapy/blob/main/src/ada/cad/__init__.py" "AdacppBackend"
    click OCC href "https://github.com/Krande/adapy/tree/main/src/ada/occ" "pythonocc backend"
    click NGEOM href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/ngeom" "NGEOM serialisation and native export"
```

`ShapeHandle` is opaque: callers never touch kernel types directly. `to_occ_shape()` is the
documented way out to a raw pythonocc shape for code that really needs one. adapy itself
does not depend on adacpp. When it is installed, the IFC/STEP readers and writers, NGEOM
export, mesh optimisation, joint detection and the FEA beam-solid tessellation use it.

## From model to GLB

```mermaid
sequenceDiagram
    autonumber
    participant U as Part.to_gltf() / show()
    participant SC as visit/scene_converter.py<br/>SceneConverter
    participant SH as visit/scene_handling/*
    participant T as visit/tessellate.py<br/>BatchTessellator
    participant B as ada.cad backend
    participant GS as visit/gltf<br/>GraphStore · meshopt
    participant OUT as GLB

    U->>SC: SceneConverter(source, RenderParams)
    SC->>SC: build_scene(): new GraphStore
    SC->>SH: dispatch on source type
    Note over SH: scene_from_part_or_assembly<br/>scene_from_object · scene_from_fem<br/>scene_from_fem_results · scene_from_step_stream
    SH->>T: tessellate_part → batch_tessellate
    loop per object
        T->>T: tessellate_geom():<br/>1 direct line mesh<br/>2 pre-triangulated geometry<br/>3 NGEOM stream (ADA_STREAM_TESS_PIPELINE)<br/>4 kernel build + BRepMesh
        T->>B: tessellate / tessellate_batch
        B-->>T: BatchMesh
    end
    T-->>SH: meshes grouped by colour/material
    SH->>GS: nodes + merged meshes (draw ranges per object)
    SC->>OUT: build_glb(): trimesh export("glb")
    SC->>OUT: ADA_EXT_data extension (if embed_ada_extension)
```

Objects that share a material are merged into one glTF mesh, and per-object *draw ranges*
are kept so the viewer can still pick, hide and colour single objects. The `ADA_EXT_data`
extension (schemas in `src/gltf_extension_schema/`, pydantic models in `ada.extension`)
carries the design and simulation metadata next to the geometry: object hierarchy, draw
ranges, FEM concepts and simulation results.

For FE meshes and results, `scene_from_fem` / `scene_from_fem_results` build the scene from
`ada.fem.results.common.Mesh` (`Mesh.create_mesh_stores()` → `MergedMesh` points, lines,
faces and optional solid beams) instead of tessellating B-rep.

## Big-file paths

Some sources never become an `Assembly`:

| Path | What it does |
|---|---|
| `cadit/step/native_step_to_glb.py`, `step2glb_capi` | STEP → GLB inside adacpp. |
| `cadit/step/stream_to_glb.py` + `glb_spill.GlbSpillStore` | STEP streamed through OCC; meshes spill to disk and the GLB is written from the spill (`write_glb_from_spill`), so memory stays bounded. |
| `cadit/ifc/native_ifc_to_glb.py` | IFC → GLB via adacpp. |
| `visit/scene_handling/scene_from_step_stream.py` | Scene from a streamed STEP. |
| `fem/results/artefacts` | FEA results → mesh GLB + per-step field blobs (see [FEA](fea.md#viewer-bake)). |

## Renderers

| Renderer | Where | Used for |
|---|---|---|
| `RendererReact` | `visit/rendering/renderer_react.py` | `obj.show()` in Jupyter: the viewer bundle (`resources/index.zip`) with the GLB inlined, in an `<iframe srcdoc>`. |
| `WebSocketRenderer` | `visit/rendering/renderer_widget.py` | The viewer connected to a running `wsock` server. |
| pygfx offscreen | `visit/rendering/render_pygfx.py`, `fea_offscreen.py` | Headless PNG posters (for example the FEA verification report's mode shapes). |
| `renderer_manager` | `visit/renderer_manager.py` | Chooses between notebook embedding and an external viewer. |
