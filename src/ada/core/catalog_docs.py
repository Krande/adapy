"""Equipment-type and system-template catalog DOCUMENTS: the pydantic schema
plus the ``validate_*_doc`` normalizers.

The equipment doc carries a bounding box, mass, IFC element class and a
port/nozzle list; the system doc carries the service category/type/medium/
voltage and the routed-segment rendering knobs. The same documents are
produced by the DEXPI reader (``ada.cadit.dexpi``), mirrored by
``ada.topo_model.equipment`` and stored per scope by the REST layer
(``ada.comms.rest.catalog``), which is why they live here, below all three.

Pure-pydantic (no ``ada`` import beyond this package, no numpy) so it runs in
the slim API image; the port geometry semantics mirror
``ada.api.systems.ports.Port``.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _equipment_doc_model():
    import re
    from typing import List, Literal, Optional

    from pydantic import BaseModel, ConfigDict, Field, conlist, field_validator

    Vec3 = conlist(float, min_length=3, max_length=3)
    _HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

    class CatalogPort(BaseModel):
        model_config = ConfigDict(extra="allow")

        name: str
        position: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
        direction_vector: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
        direction: Literal["IN", "OUT", "INOUT"] = "INOUT"
        category: Literal["process", "electrical", "signal"] = "process"
        # Optional per-port colour override as ``#rrggbb``; ``None`` means the
        # frontend derives the colour from ``category``.
        color: Optional[str] = None
        # Process identity of the nozzle, mirroring ada.Port: its tag in the source
        # definition, nominal diameter in **metres** (not DN millimetres) and piping
        # class. Round-tripped under extra="allow" before they were declared here;
        # declaring them validates them and exposes them to the port editor.
        tag: Optional[str] = None
        nominal_diameter: Optional[float] = None
        spec: Optional[str] = None

        @field_validator("color")
        @classmethod
        def _check_color(cls, v):
            if v is None:
                return v
            v = v.strip().lower()
            if not _HEX_RE.match(v):
                raise ValueError(f"color must be a '#rrggbb' hex string, got {v!r}")
            return v

    class BBox(BaseModel):
        lx: float = 1.0
        ly: float = 1.0
        lz: float = 1.0

    class EquipmentTypeDoc(BaseModel):
        # See the note on ProceduralDoc in .procedural: a normalizer that DROPS
        # what it does not recognise turns a stale field name into a no-op with
        # no error, and (for anything keyed on the normalized dict) into a hash
        # collision. Round-trip unknown keys instead; declared fields below are
        # still validated, so a malformed known field is still a loud 422.
        model_config = ConfigDict(extra="allow")

        bbox: BBox = Field(default_factory=BBox)
        mass: float = 1000.0
        # equipment-local centre of gravity; defaults to the bbox centroid at
        # compile time when omitted
        cog: Optional[Vec3] = None
        ifc_element_class: str = "IfcBuildingElementProxy"
        # Whether the linked CAD asset is authored in adapy's Z-up convention.
        # True (default) = take the asset verbatim (Z is height). False = the CAD
        # is glTF-spec Y-up and gets re-oriented Y-up→Z-up before measuring and
        # splicing. Only meaningful for mesh assets (.glb/.gltf/.stl/.obj).
        cad_z_up: bool = True
        ports: List[CatalogPort] = Field(default_factory=list)

    return EquipmentTypeDoc


@lru_cache(maxsize=1)
def _system_doc_model():
    from typing import Literal, Optional

    from pydantic import BaseModel, ConfigDict

    class SystemTemplateDoc(BaseModel):
        # Round-trip unknown keys rather than deleting them — see EquipmentTypeDoc.
        model_config = ConfigDict(extra="allow")

        type: Literal["piping", "duct", "cable", "electrical"] = "piping"
        medium: Optional[str] = None
        # electrical service voltage in volts (informational; only meaningful
        # for electrical systems)
        voltage: Optional[int] = None
        pipe_radius: float = 0.05
        pipe_wt: float = 0.005

    return SystemTemplateDoc


def validate_equipment_doc(doc: dict) -> dict:
    """Validate + normalize an equipment-type document. Raises ValueError with
    the pydantic error text on invalid input; enforces unique port names."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        model = _equipment_doc_model()(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    names = [p.name for p in model.ports]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate port names: {sorted(dupes)}")
    for p in model.ports:
        if not p.name.strip():
            raise ValueError("every port needs a non-empty name")
    return model.model_dump(mode="json")


def validate_system_doc(doc: dict) -> dict:
    """Validate + normalize a system-template document."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        model = _system_doc_model()(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    return model.model_dump(mode="json")
