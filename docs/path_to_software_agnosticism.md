# Path to Software Agnosticism

The aim is that you can start a design in the tool of your choice and carry it, without
retyping, into any FE solver adapy supports and back out to any CAD or BIM tool that needs
it. adapy is the hub in the middle: every format is read into the same object model and
written out of it, so any input can reach any output.

The diagrams below show which formats go in and out, and through which functions. Boxes are
underlined where they link to the code or to the page that explains them. **Enlarge** a
diagram to zoom, and hover a box to trace what it connects to.

## CAD and BIM

```mermaid
flowchart LR
    subgraph inputs["Read"]
        direction TB
        I_IFC[".ifc<br/>IFC2x3 · IFC4 · IFC4x3"]
        I_STEP[".step / .stp"]
        I_SAT[".sat (ACIS)"]
        I_XML["Genie .xml<br/>concept model"]
        I_GNX[".gnx<br/>Genie workspace"]
        I_DEX["DEXPI .xml<br/>P&ID"]
        I_PY["Python code"]
    end

    ASM(["ada Assembly<br/>Part tree · beams · plates · shapes<br/>pipes · equipment · FEM"])

    subgraph outputs["Write"]
        direction TB
        O_IFC[".ifc"]
        O_STEP[".step"]
        O_XML["Genie .xml"]
        O_GNX[".gnx<br/>(concept XML + ACIS body)"]
        O_DEX["DEXPI .xml"]
        O_MAC["AVEVA E3D .mac"]
        O_GLB[".glb / .gltf<br/>+ ADA_EXT_data"]
    end

    I_IFC -- "from_ifc" --> ASM
    I_STEP -- "from_step" --> ASM
    I_SAT -- "from_acis" --> ASM
    I_XML -- "from_genie_xml" --> ASM
    I_GNX -- "from_gnx" --> ASM
    I_DEX -- "from_dexpi" --> ASM
    I_PY --> ASM

    ASM -- "to_ifc" --> O_IFC
    ASM -- "to_stp" --> O_STEP
    ASM -- "to_genie_xml" --> O_XML
    ASM -- "to_gnx" --> O_GNX
    ASM -- "to_dexpi" --> O_DEX
    ASM -- "to_aveva_mac" --> O_MAC
    ASM -- "to_gltf" --> O_GLB

    I_STEP -. "native stream (adacpp)" .-> O_GLB
    I_IFC -. "native stream (adacpp)" .-> O_GLB

    click ASM href "architecture/core_model/" "The object model every format reads into"
    click I_IFC href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/ifc" "IFC reader and writers"
    click O_IFC href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/ifc" "IFC reader and writers"
    click I_STEP href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/step" "STEP readers, writers and streams"
    click O_STEP href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/step" "STEP readers, writers and streams"
    click I_SAT href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/sat" "ACIS SAT reader"
    click I_XML href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/gxml" "Genie XML"
    click O_XML href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/gxml" "Genie XML"
    click I_GNX href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/gxml" "Genie workspace"
    click O_GNX href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/gxml" "Genie workspace"
    click I_DEX href "dexpi/" "From P&ID to 3D model"
    click O_DEX href "dexpi/" "From P&ID to 3D model"
    click O_MAC href "https://github.com/Krande/adapy/tree/main/src/ada/cadit/e3d" "AVEVA E3D macro writer"
    click O_GLB href "architecture/geometry_and_visualisation/#from-model-to-glb" "Model to GLB"
    click I_PY href "notebooks/design/parts_and_assemblies/" "Parts and assemblies"
```

## Finite element analysis

```mermaid
flowchart LR
    ASM(["ada Assembly"])

    subgraph mesh["Mesh"]
        direction TB
        GMSH["gmsh<br/>Part.to_fem_obj()"]
        FEM(["FEM<br/>nodes · elements · sets · sections<br/>loads · BCs · steps"])
    end

    subgraph decks["Solver decks"]
        direction TB
        D_ABA["Abaqus .inp"]
        D_CCX["CalculiX .inp"]
        D_CA["Code_Aster .med + .comm"]
        D_SES["Sesam .FEM"]
        D_USF["Usfos .fem"]
        D_OC["OpenCourant .rad<br/>starter + engine"]
    end

    subgraph solvers["Solvers"]
        direction TB
        S_ABA[["Abaqus"]]
        S_CCX[["CalculiX"]]
        S_CA[["Code_Aster"]]
        S_SES[["Sesam / Sestra"]]
        S_OC[["OpenCourant<br/>explicit dynamics"]]
    end

    subgraph results["Results"]
        direction TB
        R_ODB[".odb"]
        R_FRD[".frd"]
        R_RMED[".rmed"]
        R_SIN[".SIN / .SIF"]
        R_ANIM[".radanim<br/>time history"]
    end

    RES(["FEAResult<br/>mesh + fields"])

    subgraph post["Post-processing"]
        direction TB
        P_SHOW["res.show()<br/>viewer"]
        P_ART["viewer artefacts<br/>(Ada Studio)"]
    end

    ASM --> GMSH --> FEM
    D_ABA -- "from_fem" --> FEM
    D_CA -- "from_fem" --> FEM
    D_SES -- "from_fem" --> FEM

    FEM -- "to_fem" --> D_ABA & D_CCX & D_CA & D_SES & D_USF & D_OC
    D_ABA --> S_ABA --> R_ODB
    D_CCX --> S_CCX --> R_FRD
    D_CA --> S_CA --> R_RMED
    D_SES --> S_SES --> R_SIN
    D_OC --> S_OC --> R_ANIM

    R_ODB & R_FRD & R_RMED & R_SIN & R_ANIM -- "from_fem_res" --> RES
    RES -- "show" --> P_SHOW
    R_RMED & R_SIN & R_ANIM -- "streaming bake" --> P_ART

    click GMSH href "architecture/fea/#meshing-a-design-model" "Meshing a design model"
    click FEM href "architecture/fea/#the-fe-model-adafem" "The FE model"
    click D_ABA href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/abaqus" "Abaqus read/write/execute/results"
    click D_CCX href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/calculix" "CalculiX write/execute/results"
    click D_CA href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/code_aster" "Code_Aster read/write/execute/results"
    click D_SES href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/sesam" "Sesam read/write/execute/results"
    click D_USF href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/usfos" "Usfos writer"
    click D_OC href "https://github.com/Krande/adapy/tree/main/src/ada/fem/formats/opencourant" "OpenCourant write/execute/results"
    click S_CA href "fea/software/" "Installing the solvers"
    click S_CCX href "fea/software/" "Installing the solvers"
    click S_OC href "fea/software/#opencourant" "OpenCourant: explicit dynamics"
    click RES href "architecture/fea/#results-adafemresults" "FEAResult"
    click P_ART href "architecture/fea/#viewer-bake" "The streaming viewer bake"
```

The FE model can also be filled from an existing deck (`ada.from_fem`), so one solver's
deck becomes another's: read an Abaqus `.inp`, write a Sesam `.FEM`. Code_Aster, CalculiX
and OpenCourant (explicit dynamics) are open source (see [FEA Software](fea/software.md)).
Abaqus and Sesam need their own licences.

## One fluent operation

The original goal still holds: design in Python, hand the same model to a BIM tool and an FE
solver, and look at the results, all in one script:

```python
import ada
from ada.base.types import GeomRepr

bm = ada.Beam("bm1", (0, 0, 0), (3, 0, 0), "IPE400", ada.Material("S420"))
p = ada.Part("structure") / bm
a = ada.Assembly("cantilever") / p

a.to_ifc("cantilever.ifc")  # to any IFC viewer or BIM tool
a.to_stp("cantilever.stp")  # to any CAD tool

bm.concept_fem.fix_end("n1")  # a support on the design object, not on mesh nodes
p.fem = p.to_fem_obj(0.1, GeomRepr.LINE)  # mesh with gmsh
a.fem.add_step(ada.fem.StepEigen("eig", num_eigen_modes=10))
res = a.to_fem("cantilever", "code_aster", overwrite=True, execute=True)

res.show()  # the mode shapes in the viewer: a browser tab from a script, inline in Jupyter
```

```mermaid
sequenceDiagram
    autonumber
    participant P as Python
    participant A as ada Assembly
    participant B as BIM / CAD tool
    participant F as FE solver
    participant V as Viewer
    P->>A: build beams, plates, parts
    A->>B: to_ifc() / to_stp()
    A->>A: to_fem_obj() · mesh with gmsh
    A->>F: to_fem(..., execute=True) · write deck, run
    F-->>A: result file → FEAResult
    A->>V: res.show()
```

## Format matrix

| Format | Extensions | Read | Write | Python | `ada convert` name |
|---|---|:-:|:-:|---|---|
| IFC | `.ifc` | ✓ | ✓ | `from_ifc` / `Assembly.to_ifc` | `ifc` |
| STEP | `.step`, `.stp` | ✓ | ✓ | `from_step` / `to_stp` | `step` |
| ACIS | `.sat`, `.acis` | ✓ | inside `.gnx` | `from_acis` | `acis` |
| Genie XML | `.xml` | ✓ | ✓ | `from_genie_xml` / `to_genie_xml` | `xml` |
| Genie workspace | `.gnx` | ✓ | ✓ | `from_gnx` / `to_gnx` | `gnx` |
| DEXPI | `.xml` | ✓ | ✓ | `from_dexpi` / `to_dexpi` | — |
| AVEVA E3D macro | `.mac` | | ✓ | `Part.to_aveva_mac` | — |
| glTF | `.glb`, `.gltf` | | ✓ | `to_gltf` | `gltf` |
| Abaqus | `.inp` | ✓ | ✓ | `from_fem` / `to_fem(…, "abaqus")` | `abaqus` |
| CalculiX | `.inp` | | ✓ | `to_fem(…, "calculix")` | `calculix` |
| Code_Aster | `.med`, `.rmed` | ✓ | ✓ | `from_fem` / `to_fem(…, "code_aster")` | `code_aster` |
| Sesam | `.FEM`, `.SIF` | ✓ | ✓ | `from_fem` / `to_fem(…, "sesam")` | `sesam` |
| Usfos | `.fem` | | ✓ | `to_fem(…, "usfos")` | `usfos` |
| OpenCourant | `.rad` | | ✓ | `to_fem(…, "opencourant")` | `opencourant` |
| FE results | `.rmed`, `.frd`, `.SIN`, `.SIF`, `.odb`, `.radanim` | ✓ | | `from_fem_res`, then `.show()` | — |

The same conversions are available from the command line, without writing any Python:
`ada convert model.ifc model.FEM` (see [Command line interface](cli.md)).
