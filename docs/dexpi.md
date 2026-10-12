# From P&ID to 3D model

[DEXPI](https://dexpi.org/) (Data Exchange in the Process Industry) is the vendor-neutral
exchange specification for a P&ID: which equipment exists, which nozzles it has, and which piping,
signal and electrical lines connect them -- with no notion whatsoever of physical size or plant
coordinates. `ada.cadit.dexpi` reads and writes it, and `ada.from_dexpi` closes the gap between
"here is a P&ID" and "here is a routed 3D model": it resolves each item to a physical equipment
definition, generates a plausible topology to put the equipment in, turns the P&ID connectivity into
adapy systems, and routes them.

Nothing here is imported by `import ada` -- `ada.cadit.dexpi` and its readers load lazily, so
the DEXPI support costs nothing until it is used.

The examples below use the `files/dexpi_files/unit_separator_proteus.xml` fixture. Both DEXPI 1.3/1.4
(Proteus XML) and DEXPI 2.0 files are accepted; the flavour is detected from the root tag.

## One call

```python
import ada
from ada.topo_model.build_spec import ProceduralBuildSpec
from ada.topo_model.layout import LayoutRules

spec = ProceduralBuildSpec(layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0))
a = ada.from_dexpi("files/dexpi_files/unit_separator_proteus.xml", spec=spec)
a.to_ifc("separator_unit.ifc")
```

## Read the P&ID

`SystemModel.from_dexpi` reads and resolves without building anything, so you can inspect it first.

```python
model = ada.SystemModel.from_dexpi("files/dexpi_files/unit_separator_proteus.xml")

print(sorted(eq.name for eq in model.equipment))
# ['E-201', 'P-201A', 'P-201B', 'PV-202.01', 'TE-203', 'TE-204', 'V-201']
print(sorted(s.name for s in model.systems))
# ['201/1', '202/1', '206/1', 'branch-TE-203', 'branch-TE-204']
```

Each equipment item has a size and one port per DEXPI nozzle, but no position yet.

```python
v = model.equipment_by_name["V-201"]
print((v.lx, v.ly, v.lz), [p.name for p in v.ports])
# (5.0, 2.5, 2.5) ['n1', 'n2', 'n3', 'n4', 'n5']
```

Anything the read could not carry is listed in `model.report` (and summarised in one warning).

```python
print(model.report.format())
# 1 of 6 system(s) and 0 of 7 equipment did not reach the 3D model:
#
# Kind    Name   Stage         Reason
# ------  -----  ------------  -----------------------------------------------------------------
# system  205/1  connectivity  1 endpoint(s) outside the segment; a routed run needs exactly two
```

Pass `strict=True` to raise instead.

## Override equipment sizes

A P&ID does not say how big anything is, so the shipped per-class sizes are placeholders. Pass
`definitions` -- a JSON/XLSX definition list, a dict, or a function of the DEXPI item:

```python
def taller(item):
    if item.tag == "V-201":
        return {"bbox": {"lx": 3.0, "ly": 3.0, "lz": 9.0}}
    return None  # fall back to the class default

model = ada.SystemModel.from_dexpi("files/dexpi_files/unit_separator_proteus.xml", definitions=taller)
```

## Build the 3D model

`to_assembly` generates decks, places the equipment and routes the systems; build-stage gaps are
reported on the assembly.

```python
a = model.to_assembly(spec)
print(a.metadata["build"]["stats"], a.metadata["build"]["issues"])
# {'systems': 5, 'equipment': 7} []
```

The layout is shelf packing by footprint, with no process sense -- expect to move things.
`ProceduralBuildSpec(route=False)` places equipment without routing, and `relocate=True` moves
equipment to clear runs that failed to route.

## Export and view

```python
a.to_ifc("separator_unit.ifc")
a.show()
```

## Write back to DEXPI

The model, not the assembly, is written back: DEXPI has no coordinates. Edits made in Python are
merged into the source file, and everything adapy does not model is kept verbatim.

```python
model.to_dexpi("separator_unit.xml")
```

## Licensing

The generated DEXPI class table and the vendored official test files under
`files/dexpi_files/vendor/` are CC BY 4.0, copyright DEXPI e.V. See the repository-root `NOTICE`
file for the full attribution.
