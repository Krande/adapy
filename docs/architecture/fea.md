# FEA

adapy covers the whole finite element loop: model → mesh → solver deck → run → results →
post-processing and visualisation. This page follows that loop through the code.

```mermaid
flowchart TB
    subgraph design["Design model"]
        PART["Part / Assembly<br/>beams · plates · shapes"]
        CONC["ConceptFEM<br/>concept loads & constraints"]
    end
    subgraph mesh["Meshing"]
        GMSH["fem/meshing<br/>GmshSession"]
    end
    subgraph femodel["FE model"]
        FEM["FEM on Part.fem<br/>nodes · elements · sets · sections<br/>steps · loads · bcs · constraints"]
        STORE["api/mesh · MeshArrays<br/>(packed numpy store)"]
    end
    subgraph solve["Solver"]
        DECK["deck writer<br/>to_fem_*"]
        RUN["execute_fem<br/>run_*"]
    end
    subgraph post["Results"]
        READ["result readers<br/>read_*_file"]
        RES["FEAResult<br/>Mesh + field data"]
        BAKE["artefact bake<br/>FEAStreamReader → bake_artefacts"]
    end
    DECKIN[(".inp / .fem / .med")]
    RESF[(".rmed / .frd / .SIN / .SIF / .odb / .radanim")]
    VIEW(["viewer · GLB · VTU"])

    PART --> GMSH --> FEM
    CONC --> FEM
    DECKIN -- "from_fem" --> FEM
    FEM --- STORE
    FEM --> DECK --> RUN --> RESF
    RESF --> READ --> RES --> VIEW
    RESF --> BAKE --> VIEW
    DECKIN --> BAKE

    click PART href "architecture/core_model/" "The object model"
    click CONC href "https://github.com/Krande/adapy/tree/main/src/ada/fem/concept" "ConceptFEM: concept-level loads and constraints"
    click GMSH href "https://github.com/Krande/adapy/tree/main/src/ada/fem/meshing" "GmshSession and partitioning"
    click FEM href "https://github.com/Krande/adapy/blob/main/src/ada/fem/base.py" "class FEM"
    click STORE href "https://github.com/Krande/adapy/tree/main/src/ada/api/mesh" "MeshArrays, ArrayNodes/ArrayElements, proxies"
    click DECK href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/general.py" "write_to_fem and the per-solver dispatch"
    click RUN href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/execute.py" "execute_fem"
    click READ href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/postprocess.py" "postprocess → FEAResult"
    click RES href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/common.py" "FEAResult, Mesh, FemNodes, ElementBlock"
    click BAKE href "https://github.com/Krande/adapy/tree/main/src/ada/fem/results/artefacts" "Streaming viewer bake"
```

## The FE model (`ada.fem`)

`ada.fem.base.FEM` is a dataclass owned by every `Part` (`part.fem`):

```mermaid
classDiagram
    direction LR
    class FEM {
        nodes: Nodes | ArrayNodes
        ref_points: Nodes
        elements: FemElements | ArrayElements
        sets, ref_sets: FemSets
        sections: FemSections
        masses, surfaces, amplitudes
        connector_sections, intprops, interactions
        predefined_fields, lcsys, constraints
        bcs: list~Bc~
        steps: list~Step~
        springs (property)
        parent: Part
        add_elem / add_section / add_bc / add_step …
        get_all_loads() · get_all_bcs()
        to_mesh() Mesh
        __add__(other) FEM
    }
    class Step {
        <<abstract>>
    }
    class StepImplicitStatic
    class StepImplicitDynamic
    class StepExplicit
    class StepEigen
    class StepEigenComplex
    class StepSteadyState
    class Load {
        LoadPoint · LoadPressure
        LoadGravity · LoadCase
    }
    FEM "1" o-- "*" Step
    Step <|-- StepImplicitStatic
    Step <|-- StepImplicitDynamic
    Step <|-- StepExplicit
    Step <|-- StepEigen
    Step <|-- StepEigenComplex
    Step <|-- StepSteadyState
    Step o-- Load
```

| Module | Contents |
|---|---|
| `fem/base.py` | `FEM` |
| `fem/containers.py` | `FemElements`, `FemSets`, `FemSections` (object containers); nodes live in `api/containers/nodes.py` (`Nodes`) |
| `fem/elements.py`, `fem/shapes/` | `Elem`, `Mass`, `Spring`, `Connector`; `LineShapes`, `ShellShapes`, `SolidShapes`, `ConnectorTypes`, `ShapeResolver`, `ElemShape`, node ordering |
| `fem/formulations/` | `Formulation`, `BeamTheory` |
| `fem/steps.py` | The step types above, `StepSolverOptions` |
| `fem/loads/` | `Load`, `LoadPressure`, `LoadGravity`, `LoadPoint`, `LoadCase` |
| `fem/concept/` | `ConceptFEM` (on `Part.concept_fem`), `BeamConceptFEM` (`Beam.concept_fem.fix_end()` …), constraint concepts (point, beam end, curve, rigid link) and load concepts (point, line, surface, acceleration field, load cases, combinations) |
| `fem/meshing/` | `GmshSession`, partitioning strategies, `multisession_gmsh_tasker` |
| `fem/concat.py` | `concatenate_fem_meshes`, `concatenate_fem_to_single_part` |
| `fem/conformality.py` | `check_conformal_mesh` |

Concept-level constraints are converted to FE constraints by
`fem/concept/to_fem.add_constraint_concepts_to_fem`. Concept loads are carried to and from
Genie XML (`cadit/gxml`).

### Meshing a design model

`Part.to_fem_obj()` drives gmsh:

```mermaid
sequenceDiagram
    autonumber
    participant P as Part.to_fem_obj
    participant G as GmshSession
    participant F as FEM
    P->>G: open session, add_obj(beams, plates, shapes)
    P->>G: partition_plates() · partition_beams()
    P->>G: mesh(size, use_quads, use_hex)
    G->>F: get_fem() → nodes, elements, sections
    P->>F: add point masses
    P->>F: remove_standalones()
    P->>F: add_constraint_concepts_to_fem()
    P->>F: check_conformal_mesh()
```

## The array-backed mesh store (`ada.api.mesh`)

With `Config().meshing_array_backed` on (the default; opt out with
`ADA_MESHING_ARRAY_BACKED=false`), a `FEM`'s nodes and elements are thin facades over one
packed numpy store, `MeshArrays`. `Node` / `Elem` objects are minted on demand as lazy
proxies.

```mermaid
classDiagram
    direction LR
    class FEM {
        nodes: ArrayNodes
        elements: ArrayElements
    }
    class ArrayNodes {
        store: MeshArrays
        from_id(id) NodeProxy
        renumber(map) · move(…)
        to_fem_nodes() FemNodes
    }
    class ArrayElements {
        store: MeshArrays
        _overflow: Mass / Spring / Connector
        renumber(map)
        to_elem_blocks() list~ElementBlock~
    }
    class MeshArrays {
        coords: float64[n,3]
        node_ids: int64[n]
        blocks: dict~ctype, ElemArrayBlock~
        id2idx (lazy)
        adjacency: CSRAdjacency (lazy)
        proxy caches (weak)
    }
    class ElemArrayBlock {
        ctype
        conn: int32[m,k] row indices
        el_ids: int64[m]
        fem_secs · elsets · formulations
        ecc · hinge · metadata (sparse)
    }
    class NodeProxy {
        store, row
        id → node_ids[row]
        p → coords[row]
    }
    class ElemProxy {
        store, ctype, row
    }
    FEM --> ArrayNodes
    FEM --> ArrayElements
    ArrayNodes --> MeshArrays
    ArrayElements --> MeshArrays
    MeshArrays "1" o-- "*" ElemArrayBlock
    ArrayNodes ..> NodeProxy : mints
    ArrayElements ..> ElemProxy : mints
```

- **Connectivity holds row indices, not node ids.** Renumbering nodes only rewrites
  `node_ids`. Handing the mesh to the results side (`FEM.to_mesh()`) is zero-copy:
  `to_elem_blocks()` passes `conn` and `el_ids` through as
  `ElementBlock(..., node_refs_are_indices=True)`.
- **Node→element adjacency** is a CSR incidence (`CSRAdjacency`), built lazily, instead of
  per-node reference lists.
- **The Sesam, Abaqus and Code_Aster readers** fill a `MeshArrays` directly from the deck
  (Sesam can stream the file). Other FEMs are converted with `to_array_backed(fem)`.
  `GmshSession.get_fem()` still produces object containers, which
  `fem/formats/utils.convert_part_objects` converts.
- **`fem/concat.py`** merges multi-part models at store level, offsetting ids and re-keying
  sets, sections, boundary conditions and masses.

## Solver integration

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Assembly.to_fem
    participant W as general.write_to_fem
    participant X as execute.execute_fem
    participant S as Solver
    participant P as postprocess.postprocess
    participant R as FEAResult

    U->>A: to_fem(name, fem_format, execute=True)
    A->>W: merge parts (except Abaqus) → Setup.default_pre_processor
    W-->>A: analysis_dir/deck
    A->>X: Setup.default_executor
    X->>S: run solver, write run_log.txt
    S-->>X: result file
    A->>P: Setup.default_post_processor
    P-->>R: FEAResult
    R-->>U: to_gltf · to_vtu · show · get_eig_summary
```

`ada.from_fem_res(path)` starts from an existing result file at the `postprocess` step.
[Interoperability](interop.md#finite-element-formats) lists each solver's functions. Abaqus
runs can wait for FlexNet licence tokens (`abaqus/licensing.abaqus_license_slot`, opt-in via
`ADA_ABAQUS_LICENSE_WAIT_S`).

## Results (`ada.fem.results`)

Every result reader produces the same read-only snapshot:

```mermaid
classDiagram
    direction LR
    class FEAResult {
        name
        software: FEATypes
        results: list~NodalFieldData | ElementFieldData~
        mesh: Mesh
        step_name_map
        eigen_mode_data
        to_gltf() · to_vtu() · show()
        get_eig_summary() EigenDataSummary
    }
    class Mesh {
        elements: list~ElementBlock~
        nodes: FemNodes
        elem_data · sections · materials · vectors
        sets · eccentricities
        create_mesh_stores() MeshStore
    }
    class FemNodes {
        coords: float[n,3]
        identifiers: int[n]
    }
    class ElementBlock {
        elem_info: ElementInfo
        node_refs: int[m,k]
        identifiers: int[m]
        node_refs_are_indices: bool
    }
    class NodalFieldData
    class ElementFieldData
    FEAResult --> Mesh
    FEAResult o-- NodalFieldData
    FEAResult o-- ElementFieldData
    Mesh --> FemNodes
    Mesh "1" o-- "*" ElementBlock
```

| Module | Contents |
|---|---|
| `common.py` | `FEAResult`, `Mesh`, `FemNodes`, `ElementBlock`, `ElementInfo`, `MeshStore` |
| `field_data.py` | `NodalFieldData`, `ElementFieldData`, `FieldPosition`, line-section integration points |
| `eigenvalue.py` | `EigenDataSummary`, `EigenMode` |
| `sqlite_store.py` | `SQLiteFEAStore` |
| `case_result.py`, `line_sections.py` | Cached per-case results; beam line-section tables |
| `docs.py` | FEA bundles for the verification report (`bake_fea_bundles`, `collect_fea_bundles`, `restore_fea_bundles`) |

Result readers use the ids from the source file. `node_refs_are_indices=False` (the
default) means `node_refs` holds node ids, which consumers map to rows. Only
`FEM.to_mesh()` produces row-index blocks.

## Viewer bake

Large results are not loaded into the browser as one GLB. The **bake**
(`fem/results/artefacts`) streams a result source into a set of files that the viewer fetches
piece by piece:

```mermaid
flowchart TB
    SRC["source file"] --> REG{"readers.make_stream_reader<br/>(by suffix)"}
    REG -- ".rmed" --> R1["RmedStreamReader"]
    REG -- ".sif" --> R2["SifStreamReader<br/>(adapter if ADA_FEA_SIF_STREAMER=0)"]
    REG -- ".sin" --> R3["FEAResultStreamAdapter(read_sin_file)<br/>SinStreamReader for steps= or ADA_FEA_SIN_STREAMER=1"]
    REG -- ".radanim" --> R6["make_radanim_stream_reader<br/>OpenCourant time history (transient)"]
    REG -- ".inp .fem .med" --> R4["_make_fem_reader<br/>from_fem → concatenate_fem_meshes<br/>(mesh only, plus property fields)"]
    REG -- "register_stream_reader()" --> R5["plugins (e.g. .odb)"]
    R1 & R2 & R3 & R4 & R5 & R6 --> P["FEAStreamReader protocol<br/>read_mesh_geometry · field_specs · iter_field_steps<br/>element_field_specs · iter_element_field_steps<br/>try_solid_beams · try_history_records"]
    P --> B["bake.bake_artefacts()"]
    B --> M["fea.mesh.glb<br/>+ edges · line_edges · elements"]
    B --> F["fea.FIELD.bin (nodal, AFBL)<br/>fea.FIELD.ETYPE.elements.bin (AFEL)<br/>step-major float32"]
    B --> BS["fea.beam_solids.compact.bin (AFBS)<br/>or beam-solid GLB + warp + elements"]
    B --> MF["fea.manifest.json (written last)<br/>fields · ranges · steps · groups"]

    click REG href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/readers.py" "Suffix → stream-reader registry"
    click R1 href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/code_aster/read/med_stream_reader.py" "RmedStreamReader"
    click R2 href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/sesam/results/sif_stream.py" "SifStreamReader"
    click R3 href "https://github.com/Krande/adapy/blob/main/src/ada/fem/formats/sesam/results/read_sin.py" "read_sin_file / SinStreamReader"
    click R4 href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/readers.py" "_make_fem_reader"
    click P href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/protocol.py" "The FEAStreamReader protocol"
    click B href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/bake.py" "bake_artefacts / bake_fea_artefacts_from_source"
    click M href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/mesh.py" "Mesh GLB and sidecar writers"
    click F href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/fields.py" "FieldBlobWriter / ElementFieldBlobWriter"
    click BS href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/beam_compact.py" "Compact beam-solid instances"
    click MF href "https://github.com/Krande/adapy/blob/main/src/ada/fem/results/artefacts/manifest.py" "build_manifest / write_manifest"
```

- Fields are written **one step at a time** (`FieldBlobWriter`, `ElementFieldBlobWriter`), so
  peak memory does not grow with the number of steps. Each blob has a fixed JSON header and
  then a contiguous `[steps × entities × components]` array that the viewer range-fetches
  per step.
- `FEAResultStreamAdapter` makes any eager reader that returns a `FEAResult` fit the protocol.
- `beam_solids.py` / `beam_compact.py` extrude beam elements to solids with their real
  section profiles. The compact format stores one instance per beam and lets the viewer
  expand them.
- Extras: `mode_normalization` (mode-shape scale factors), `step_subset`
  (`restrict_to_steps`), `history` (time-history records), `posters` (offscreen PNG posters
  via pygfx, `bake_with_posters`).
- On the platform, the `fea_artefacts` job runs `bake_fea_artefacts_from_source` and writes to
  `_derived/<source>.fea/`. The REST routes in `routes/fea.py` serve the artefacts and the
  manifest (see [Viewer platform](platform.md)).

## Verification report

`verification/` is a [paradoc](https://github.com/Krande/paradoc) project that builds the
[FEA verification report](../fea/verification.md):

```mermaid
flowchart TB
    T["verification/tasks.py<br/>@task DAG"] --> D["design"] --> ME["mesh<br/>(geom repr × order × hex/quad × reduced int.)"]
    ME --> RE["run_eig<br/>(fan-out over solvers)"]
    RE --> PP["postprocess"]
    PP --> OUT["eig_tables · modal_tables · freq_plot · fea_outputs"]
    CACHE[(".cache/ · .cache-plate/<br/>Abaqus & Sesam replays")] --> RE
    OUT --> PD["paradoc build<br/>(report/ markdown)"]
    PD --> WEB["docs/_static/fea-report/<br/>static web bundle"]
    PD --> FILES["fea-report.pdf / .docx / .odt<br/>docs/_static/fea-report-files/"]

    click T href "https://github.com/Krande/adapy/blob/main/verification/tasks.py" "The report's task DAG"
    click CACHE href "https://github.com/Krande/adapy/tree/main/verification/.cache" "Committed Abaqus / Sesam results"
    click PD href "https://github.com/Krande/adapy/blob/main/verification/paradoc.toml" "paradoc build profiles"
    click WEB href "fea/verification/" "Open the report page"
```

Code_Aster and CalculiX run on every build. Abaqus and Sesam need licences, so their results
replay from committed caches (`_CACHE_ONLY_SOLVERS`). `fea_outputs` bakes the mode-shape
artefact bundles that the report's interactive 3D views load.
