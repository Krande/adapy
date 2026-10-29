"""Write ONE selected element of a loaded model -- and everything under it -- as a STEP or IFC file.

WHAT IS SELECTED IS A NAME IN A TREE, NOT AN OBJECT. The viewer's tree is built from the GLB's
``id_hierarchy``, which is ``(name, parent)`` per node and nothing else; the GLB itself is
triangles. So the only way back from a selection to something a writer can serialise is to read
the model again -- the SOURCE file, or a provider's own objects (``ada.assets.concepts``) -- and
find the element by the name the tree showed. Both GLB writers name their nodes after the objects
they tessellated (``Part.get_graph_store`` for adapy's own, and the native IFC streamer is held to
the same names field for field), so for those sources the name IS the join.

NAMES REPEAT, so the tree's path rides along. A plate called ``PL1`` in every deck of a ship is
the normal case, not the edge one. The caller sends the names from the model's top down to the
selection and a match must END with that path; more than one survivor is refused by count rather
than one of them exported -- a file that silently holds the wrong deck is worse than no file.

THE SELECTION IS RE-PARENTED, NOT COPIED. ``copy_to`` is not implemented for every physical
object (an IFC-read ``Shape`` has none), and a copy would also have to re-derive placement. The
source model is a throwaway parse that is never written, so the selected element is simply moved
into a fresh ``Assembly`` -- placed where its ancestors put it -- and marked ADDED, which is what
both writers key on: the IFC writer only emits ADDED objects, and an object read from an IFC is
NOCHANGE because the file it came from already held it.

Leaf module on purpose: no FastAPI, so the worker, the queue-less local transport and the tests
all call the same functions.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence

__all__ = [
    "DEFAULT_EXPORT_OPTIONS",
    "IFC_SCHEMAS",
    "SELECTION_EXPORT_FORMATS",
    "SELECTION_SOURCE_EXTS",
    "STEP_SCHEMAS",
    "SelectionExportError",
    "export_options_identity",
    "parse_export_options",
    "export_asset_selection",
    "export_filename",
    "export_source_selection",
    "failure_log",
    "find_selection",
    "reparent_selection",
    "scope_to_selection",
    "selection_derived_key",
    "write_selection",
]

#: Format -> file extension. STEP and IFC are the two neutral formats adapy writes from its own
#: objects; anything else the converter offers (GLB, Genie XML, ...) is either triangles or a
#: format a "download this part" button has no business choosing for the user.
SELECTION_EXPORT_FORMATS: dict[str, str] = {"step": ".step", "ifc": ".ifc"}

#: Beside a several-element export's file: which provider its geometry came from, and the members
#: that were left out (``{geometry_provider, members, warnings}``). Also written when the export
#: FAILS with a :class:`SelectionExportError` that carries a ``detail``, as ``error`` -- then there is
#: no file beside it, and the job's own ``error`` is ``error.message``.
EXPORT_LOG_SUFFIX = ".log.json"

#: Sources whose GLB tree is named after the objects a re-read produces, so a selection can find
#: its way back. FEM decks are deliberately absent: their tree is elements and sets, not the
#: concept objects a CAD export would be made of, and a selection there has no honest meaning.
#: A ``.glb`` is absent because there is nothing behind it to re-read.
SELECTION_SOURCE_EXTS: frozenset[str] = frozenset({".ifc", ".step", ".stp", ".xml", ".gnx", ".sat", ".acis"})

# What a download may be called: the selection's own name where it is printable, never a path, and
# no spaces.
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


#: The STEP application protocols an export can declare. AP214 is what it has always written.
STEP_SCHEMAS: tuple[str, ...] = ("AP214", "AP242")

#: The IFC schemas an export can be written in, as the option names them -> the writer's name.
IFC_SCHEMAS: dict[str, str] = {"IFC4": "IFC4", "IFC4X3": "IFC4X3_add2"}

#: What an export is written with when the request names nothing (``export_options``).
DEFAULT_EXPORT_OPTIONS: dict[str, Any] = {
    "step_schema": "AP214",
    "ifc_schema": "IFC4",
    "ifc_swept_primitives": False,
    # The IFC header's file name, author and project: ``{file_name?, author?, project?}``.
    "ifc_header": None,
}

_HEADER_FIELDS = ("file_name", "author", "project")

#: Bumped when the IFC writer's output changes for the same model, so an IFC written before is not
#: served again from its old key. 2: 2D profiles, 'Clipping' bodies, revolve angles in radians.
#: 3: node relations (systems of plain members, connections, filled openings, AdaRelations).
IFC_WRITER_REVISION = 3

#: Options always part of an export's identity for their format, default or not: the default
#: changed from what earlier exports were written in, so a key without it could name either.
_ALWAYS_IN_IDENTITY: dict[str, str] = {"ifc_schema": "ifc"}


def parse_export_options(raw: Any) -> dict[str, Any]:
    """``export_options`` as the writers take them: the defaults, overridden by what ``raw`` names.
    Raises :class:`SelectionExportError` for an unknown key or value -- an option silently dropped
    would hand the user a file written some other way than they asked."""
    out = dict(DEFAULT_EXPORT_OPTIONS)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise SelectionExportError("'export_options' must be an object")
    unknown = sorted(set(raw) - set(DEFAULT_EXPORT_OPTIONS))
    if unknown:
        raise SelectionExportError(
            f"unknown export option(s) {', '.join(unknown)} (known: {', '.join(sorted(DEFAULT_EXPORT_OPTIONS))})"
        )
    if "step_schema" in raw:
        schema = str(raw["step_schema"] or "").strip().upper()
        if schema not in STEP_SCHEMAS:
            raise SelectionExportError(f"'step_schema' must be one of {', '.join(STEP_SCHEMAS)} (got {raw['step_schema']!r})")
        out["step_schema"] = schema
    if "ifc_schema" in raw:
        schema = str(raw["ifc_schema"] or "").strip().upper()
        if schema not in IFC_SCHEMAS:
            raise SelectionExportError(f"'ifc_schema' must be one of {', '.join(IFC_SCHEMAS)} (got {raw['ifc_schema']!r})")
        out["ifc_schema"] = schema
    if "ifc_swept_primitives" in raw:
        if not isinstance(raw["ifc_swept_primitives"], bool):
            raise SelectionExportError(f"'ifc_swept_primitives' must be true or false (got {raw['ifc_swept_primitives']!r})")
        out["ifc_swept_primitives"] = raw["ifc_swept_primitives"]
    if raw.get("ifc_header") is not None:
        header = raw["ifc_header"]
        if not isinstance(header, dict) or set(header) - set(_HEADER_FIELDS):
            raise SelectionExportError(f"'ifc_header' must be an object of {', '.join(_HEADER_FIELDS)}")
        if not all(v is None or isinstance(v, str) for v in header.values()):
            raise SelectionExportError("'ifc_header' values must be text")
        cleaned = {k: header[k].strip()[:200] for k in _HEADER_FIELDS if isinstance(header.get(k), str) and header[k].strip()}
        out["ifc_header"] = cleaned or None
    return out


def export_options_identity(options: dict[str, Any], fmt: str) -> dict[str, Any]:
    """The options that make this export a different FILE: those that differ from the defaults and
    apply to ``fmt`` -- so an export asked for with the defaults keeps the key it always had, and an
    IFC is not written twice for two STEP schemas."""
    applies = {
        "step_schema": fmt == "step",
        "ifc_schema": fmt == "ifc",
        "ifc_swept_primitives": fmt == "ifc",
        "ifc_header": fmt == "ifc",
    }
    out = {
        k: v
        for k, v in options.items()
        if applies.get(k, True) and (DEFAULT_EXPORT_OPTIONS.get(k) != v or _ALWAYS_IN_IDENTITY.get(k) == fmt)
    }
    if fmt == "ifc":
        out["writer"] = IFC_WRITER_REVISION
    return out


class SelectionExportError(ValueError):
    """The selection cannot be exported, with the reason a user can act on.

    ``detail``, when set, is the same failure in machine-readable form (``{code, message, ...}``):
    the job stores it as ``error`` in the export's ``.log.json`` beside where the file would have
    been, so a client can offer the fix rather than only print the sentence."""

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class _Candidate:
    obj: Any
    #: Names from the model's top down to (and including) this element, as the GLB tree shows
    #: them. A pipe SEGMENT is its own tree row but the pipe is what gets written, so a segment
    #: candidate carries the segment's chain and the pipe as its object.
    chain: tuple[str, ...]


def _chain(obj: Any) -> tuple[str, ...]:
    return tuple(str(a.name) for a in reversed(obj.get_ancestors(include_self=True)))


def _candidates(model: Any) -> Iterator[_Candidate]:
    """Every node the GLB tree of ``model`` could have shown, with its name chain.

    Mirrors ``Part.get_graph_store``: parts, then each part's own physical objects with pipes
    split into their segments -- the walk that named the tree in the first place.
    """
    for part in model.get_all_subparts(include_self=True):
        part_chain = _chain(part)
        yield _Candidate(part, part_chain)
        for obj in part.get_all_physical_objects(sub_elements_only=True):
            obj_chain = (*part_chain, str(obj.name))
            yield _Candidate(obj, obj_chain)
            for seg in getattr(obj, "segments", None) or ():
                yield _Candidate(obj, (*obj_chain, str(seg.name)))


def find_selection(model: Any, element: str, path: Sequence[str] = ()) -> Any:
    """The one object in ``model`` the tree row ``element`` (under ``path``) stands for.

    ``path`` is the names from below the model's own root row down to ``element``, inclusive;
    empty means "match on the name alone". The root row is left out by the caller because the
    viewer relabels it (a provider's model is shown by its site label, a file by its name), so
    it says nothing about the model's own top. A candidate matches when its chain ENDS with the
    path -- the GLB may carry levels above what the caller sent, never fewer.
    """
    want = tuple(str(p) for p in path) or (str(element),)
    if want[-1] != element:
        raise SelectionExportError(f"the path {list(path)!r} does not end at the selected element {element!r}")
    named = [c for c in _candidates(model) if c.chain[-1] == element]
    if not named:
        raise SelectionExportError(
            f"no element named {element!r} in the re-read model. The tree was drawn from the GLB, and "
            f"this source's objects do not carry that name -- select the model's root row to export "
            f"the whole model instead"
        )
    matched = [c for c in named if len(c.chain) >= len(want) and c.chain[-len(want) :] == want]
    # One OBJECT can surface as several rows (a pipe through its segments); those are one answer.
    unique = list({id(c.obj): c for c in matched}.values())
    if len(unique) == 1:
        return unique[0].obj
    if not unique:
        raise SelectionExportError(
            f"{len(named)} element(s) are named {element!r}, but none sits under {' / '.join(want[:-1]) or 'the top'} "
            f"-- the model read now is not the one the tree was drawn from"
        )
    where = "under the same path" if path else "and no path was sent to tell them apart"
    raise SelectionExportError(
        f"{len(unique)} elements are named {element!r} {where}, so which one was selected cannot "
        f"be told from its name -- refusing rather than exporting one of them"
    )


def _mark_added(top: Any) -> None:
    """Make every object under ``top`` something the writers will emit, with its definitions.

    ADDED because that is the IFC writer's whole filter, and the materials and sections are
    registered on the part that now owns each object because the writer resolves them there:
    a model read from a file keeps them wherever its reader put them, often on the old root.
    """
    from ada.base.changes import ChangeAction

    for part in top.get_all_subparts(include_self=True):
        part.change_type = ChangeAction.ADDED
        for obj in part.get_all_physical_objects(sub_elements_only=True):
            obj.change_type = ChangeAction.ADDED
            mat = getattr(obj, "material", None)
            if mat is not None:
                registered = part.add_material(mat)
                registered.change_type = ChangeAction.ADDED
                if registered is not mat:
                    obj.material = registered
            for attr in ("section", "taper"):
                sec = getattr(obj, attr, None)
                if sec is not None and hasattr(sec, "change_type"):
                    part.add_section(sec).change_type = ChangeAction.ADDED


def scope_to_selection(model: Any, element: str | None, path: Sequence[str] = ()) -> Any:
    """An ``Assembly`` holding the selection and everything under it, ready for a writer.

    ``element`` None means the whole model. A model that is already an ``Assembly`` is then
    returned as it is, so a whole-file export goes through exactly the writer path a plain
    conversion of that file would; a bare ``Part`` (what a provider hands back) is wrapped.
    """
    import ada

    if element is None and isinstance(model, ada.Assembly):
        return model

    out = ada.Assembly(str(getattr(model, "name", None) or "selection"), units=model.units)
    target = model if element is None else find_selection(model, element, path)
    # Beneath its whole ancestry: no element lands in the file without the levels above it.
    place_rooted(target, out, {}, keep_root=False)
    for part in list(out.parts.values()):
        _mark_added(part)
    return out


def _unique_part_name(parent: Any, name: str) -> str:
    taken = {str(p.name) for p in parent.parts.values()}
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def place_rooted(target: Any, into: Any, shells: dict[int, Any], *, keep_root: bool) -> None:
    """Move ``target`` under ``into`` beneath copies of ALL its ancestors, up to its model's top.

    Each ancestor becomes a part of the same name and placement; ``shells`` maps an ancestor to
    its copy, so selections sharing an ancestor share it in the result. The model's root
    ``Assembly`` is copied only with ``keep_root`` -- when it is dropped, its placement is folded
    into the first level kept, so everything stays where its source put it. ``target`` may be a
    part, one physical object, or a whole model (an ``Assembly``, whose top level moves).
    """
    import ada
    from ada.base.ifc_types import IFC_ENTITY_KEY

    if isinstance(target, ada.Assembly):
        holder = into
        if keep_root:
            holder = shells.get(id(target))
            if holder is None:
                holder = ada.Part(_unique_part_name(into, str(target.name)), placement=target.placement.copy_to())
                into.add_part(holder)
                shells[id(target)] = holder
        for child in list(target.parts.values()):
            holder.add_part(child)
        for direct in list(target.get_all_physical_objects(sub_elements_only=True)):
            holder.add_object(direct)
        return

    ancestors = list(reversed(target.get_ancestors(include_self=False)))
    dropped_root = bool(ancestors) and isinstance(ancestors[0], ada.Assembly) and not keep_root
    if dropped_root:
        ancestors = ancestors[1:]
    parent = into
    for i, anc in enumerate(ancestors):
        shell = shells.get(id(anc))
        if shell is None:
            placement = (
                anc.placement.get_absolute_placement(include_rotations=True).copy_to()
                if i == 0 and dropped_root
                else anc.placement.copy_to()
            )
            shell = ada.Part(_unique_part_name(parent, str(anc.name)), placement=placement)
            # The shell stands for the same element, so it is written as the same IFC entity.
            if IFC_ENTITY_KEY in anc.metadata:
                shell.metadata[IFC_ENTITY_KEY] = anc.metadata[IFC_ENTITY_KEY]
            parent.add_part(shell)
            shells[id(anc)] = shell
        parent = shell
    if isinstance(target, ada.Part):
        if parent is into and dropped_root:
            # A top-level part of a dropped root: the root's placement goes with it.
            target.placement = target.placement.get_absolute_placement(include_rotations=True).copy_to()
        parent.add_part(target)
    else:
        parent.add_object(target)


def reparent_selection(target: Any, into: Any) -> Any:
    """Move ``target`` (a part or one physical object) under ``into``, where its ancestors placed it.

    Returns the part now directly under ``into``. Shared with the clash check over a group
    (``ada.clash.group_model``), which re-parents each member into its own part the same way.
    The source model is left with a dangling entry, never re-read: callers resolve every
    selection they want from one model BEFORE moving any of them.
    """
    from ada import Part

    if isinstance(target, Part):
        # Resolved BEFORE the move: an absolute placement is accumulated through the ancestors,
        # and after the move the only ancestor left is the new, unplaced root.
        # Copied because a resolved placement may be shared with its siblings (it is documented
        # as read-only), and this one is about to become the part's own.
        absolute = target.placement.get_absolute_placement(include_rotations=True).copy_to()
        target.placement = absolute
        into.add_part(target)
        return target
    # A lone object keeps the part it sat in as its holder, so the exported file still names
    # the level it came from and the object stays where that level placed it.
    from ada.base.ifc_types import IFC_ENTITY_KEY

    owner = target.parent
    holder = Part(str(owner.name), placement=owner.placement.get_absolute_placement(include_rotations=True))
    if IFC_ENTITY_KEY in owner.metadata:
        holder.metadata[IFC_ENTITY_KEY] = owner.metadata[IFC_ENTITY_KEY]
    holder.add_object(target)
    into.add_part(holder)
    return holder


def _step_has_solids(path: pathlib.Path) -> bool:
    from .converters.mesh_step import _step_has_solids as has_solids

    return has_solids(path)


def write_selection(
    asm: Any, fmt: str, out_path: pathlib.Path, export_options: dict[str, Any] | None = None
) -> pathlib.Path:
    """Serialise ``asm`` as ``fmt``. The same writer choices the converter makes for the format.
    ``export_options`` (:func:`parse_export_options`): ``step_schema``, the protocol a STEP declares;
    ``ifc_schema``, the schema an IFC is written in; ``ifc_swept_primitives``, cylinders and cones
    written as swept solids (``ada.cadit.ifc.write.shapes.swept_primitives``)."""
    options = parse_export_options(export_options)
    if fmt == "step":
        schema = options["step_schema"]
        asm.to_stp(str(out_path), schema=schema)
        if not _step_has_solids(out_path):
            # Same retry as the converter's ifc->step leg: a body with no analytic AP242 form is
            # skipped by the B-rep writer, and the faceted streaming writer leaves nothing behind.
            asm.to_stp(str(out_path), writer="stream", fuse_fem=False, schema=schema)
        return out_path
    if fmt == "ifc":
        # The in-memory writer, never the streaming one: the streamer rebuilds the file from
        # concept objects and is only correct for a freshly built model, while a selection is
        # made of objects read from somewhere -- and is small enough not to need it.
        # The schema is read when the IFC file is first created, so a store made before (in another
        # schema) is dropped.
        from ada.api.user import User
        from ada.cadit.ifc.store import IfcStore
        from ada.cadit.ifc.write.shapes.swept_primitives import SWEPT_PRIMITIVES_KEY

        # The schema, the project and the owner history's person are read when the IFC file is first
        # created, so a store made before is dropped when any of them is set here.
        header = options["ifc_header"] or {}
        schema = IFC_SCHEMAS[options["ifc_schema"]]
        if asm.metadata.get("schema") != schema or header.get("project") or header.get("author"):
            asm.metadata["schema"] = schema
            if header.get("project"):
                asm.metadata["project"] = header["project"]
            if header.get("author"):
                asm._user = User(user_id=header["author"], given_name=header["author"])
                asm._user.parent = asm
            asm.ifc_store = None
        if header.get("file_name"):
            asm.metadata[IfcStore.HEADER_FILE_NAME_KEY] = header["file_name"]
        asm.metadata[SWEPT_PRIMITIVES_KEY] = bool(options["ifc_swept_primitives"])
        asm.to_ifc(str(out_path), streaming=False)
        return out_path
    raise SelectionExportError(
        f"unsupported export format {fmt!r} (expected one of {sorted(SELECTION_EXPORT_FORMATS)})"
    )


def export_filename(label: str | None, fmt: str) -> str:
    """What the download is called: the selection's name with the format's extension."""
    base = _UNSAFE_FILENAME_RE.sub("_", str(label or "").strip())
    base = re.sub(r"_{2,}", "_", base).strip("._-") or "selection"
    return f"{base[:100]}{SELECTION_EXPORT_FORMATS[fmt]}"


def selection_derived_key(
    content_token: str,
    target: dict[str, Any],
    *,
    fmt: str,
    element: str | None,
    path: Sequence[str],
    filename: str,
    export_options: dict[str, Any] | None = None,
    relations_token: str | None = None,
) -> str:
    """Where an export lands: one key per (source bytes, what was addressed, selection, format).

    ``relations_token`` (``db.node_relations.relations_token_for``) moves when the relations a file
    is written with change (:data:`RELATION_FORMATS`); None, and any other format, leave the key as
    it was.

    ``content_token`` moves when the source's bytes do, so a re-upload is a miss rather than a
    stale file. ``target`` is what the request addressed -- a source key, or a published node's
    collection/subject/revision/node -- because one blob is commonly shared by many subjects and a
    token over the bytes alone would hand one node's export to another. The selection's PATH is
    in the token as well as its name: two same-named plates in two decks are two files. So are the
    export options that change the file (``export_options_identity``), and only those.
    """
    identity: dict[str, Any] = {"target": target, "format": fmt, "element": element, "path": list(path)}
    differs = export_options_identity(parse_export_options(export_options), fmt)
    if differs:
        identity["options"] = differs
    if relations_token and fmt in RELATION_FORMATS:
        identity["relations"] = relations_token
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"_derived/export/{content_token}/{token}/{filename}"


def _parse_path(path: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(str(p) for p in (path or ()))


def export_source_selection(
    src_path: pathlib.Path,
    fmt: str,
    out_path: pathlib.Path,
    *,
    element: str | None = None,
    path: Iterable[Any] | None = None,
    export_options: dict[str, Any] | None = None,
) -> pathlib.Path:
    """Read a source file core reads, scope it to the selection, write it to ``out_path``."""
    from .converters.ada_load import _load_with_ada

    ext = src_path.suffix.lower()
    if ext not in SELECTION_SOURCE_EXTS:
        raise SelectionExportError(
            f"a {ext or 'extensionless'} source cannot be exported by selection: its tree is not "
            f"named after objects a re-read produces (supported: {sorted(SELECTION_SOURCE_EXTS)})"
        )
    model = _load_with_ada(src_path, ext)
    return write_selection(scope_to_selection(model, element, _parse_path(path)), fmt, out_path, export_options)


def export_asset_selection(
    *,
    collection: str,
    subject: str,
    storage: Any,
    fmt: str,
    out_path: pathlib.Path,
    revision: str | None = None,
    node: str | None = None,
    element: str | None = None,
    path: Iterable[Any] | None = None,
    export_options: dict[str, Any] | None = None,
    attributes: Any = None,
    relations: Any = None,
) -> pathlib.Path:
    """Read a published node through its provider's concepts, scope it, write it to ``out_path``.

    The provider decides what "the node" contains (``node`` is the loaded node, the same scope
    its build drew); ``element`` narrows within that by name, exactly as for a file.
    ``attributes``: the attribute lookup handed to the provider (``ada.assets.attribute_lookup``).
    ``relations``: the relation lookup an IFC is written with (``ada.assets.relation_lookup``).
    """
    from ada.clash.from_asset import part_for_asset_node

    part = part_for_asset_node(
        collection=collection, subject=subject, storage=storage, revision=revision, node=node, attributes=attributes
    )
    asm = scope_to_selection(part, element, _parse_path(path))
    _apply_relations(asm, fmt, relations, [collection])
    return write_selection(asm, fmt, out_path, export_options)


#: The formats written with the relations: IFC writes them, and both write runs in their order.
RELATION_FORMATS = frozenset({"ifc", "step"})


def _apply_relations(asm: Any, fmt: str, relations: Any, collections: Iterable[str]) -> None:
    """The relations of the elements a file is written with (``relation_lookup.apply_relations``)."""
    if fmt not in RELATION_FORMATS or relations is None:
        return
    from ada.assets.relation_lookup import apply_relations

    apply_relations(asm, relations, collections)


def group_export_derived_key(
    group_token: str,
    geometry_token: str,
    filename: str,
    export_options: dict[str, Any] | None = None,
    relations_token: str | None = None,
) -> str:
    """Where a several-element export lands: one key per (members and their sources' contents,
    the geometry they were read from, the export options that change the file, file name -- which
    carries the format -- and the relations it is written with)."""
    fmt = "step" if filename.lower().endswith((".step", ".stp")) else "ifc"
    differs = export_options_identity(parse_export_options(export_options), fmt)
    if relations_token and fmt in RELATION_FORMATS:
        differs = {**differs, "relations": relations_token}
    if differs:
        token = hashlib.sha256(json.dumps(differs, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        geometry_token = f"{geometry_token}-{token}"
    return f"_derived/export/group/{group_token}/{geometry_token}/{filename}"


def failure_log(report: dict | None, exc: BaseException) -> dict | None:
    """The ``.log.json`` a FAILED export leaves beside its (absent) file: ``report`` plus the
    failure's ``detail`` as ``error``. None when the failure carries no detail -- then only the
    job's error sentence says what happened, as before."""
    detail = getattr(exc, "detail", None)
    if not isinstance(exc, SelectionExportError) or not isinstance(detail, dict):
        return None
    return {**dict(report or {}), "error": detail}


def export_group_selection(
    group: dict[str, Any],
    *,
    storage: Any,
    fmt: str,
    out_path: pathlib.Path,
    geometry_provider: str | None = None,
    export_options: dict[str, Any] | None = None,
    attributes: Any = None,
    relations: Any = None,
) -> list[str]:
    """Write a NORMALISED group's members (``ada.clash.group.normalise_group``) as ONE file.

    The members are assembled the way a group clash check assembles them
    (``ada.clash.group_model.build_group_model``): each source read once, one part per member,
    and -- with ``geometry_provider`` -- every node member re-addressed to that provider's publish
    of the same element first. Returns the warnings: members that were left out, and why.
    """
    from ada.clash.group_model import (
        GroupModelError,
        NoProviderGeometryError,
        build_group_model,
    )

    extra = {}
    if attributes is not None:
        import functools

        from ada.clash.from_asset import part_for_asset_node

        extra["read_node"] = functools.partial(part_for_asset_node, attributes=attributes)
    try:
        built = build_group_model(group, storage=storage, geometry_provider=geometry_provider, rooted=True, **extra)
    except NoProviderGeometryError as exc:
        # The geometry was never published here: say so once, with what a request for it needs.
        action = "export again"
        raise SelectionExportError(exc.sentence(action), detail=exc.detail(action)) from exc
    except GroupModelError as exc:
        raise SelectionExportError(str(exc)) from exc
    for part in list(built.model.parts.values()):
        _mark_added(part)
    collections = [
        str(m["target"]["collection"]) for m in group.get("members") or () if m.get("target", {}).get("kind") == "node"
    ]
    _apply_relations(built.model, fmt, relations, collections)
    write_selection(built.model, fmt, out_path, export_options)
    return list(built.warnings)
