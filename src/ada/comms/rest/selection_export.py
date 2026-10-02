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
    "SELECTION_EXPORT_FORMATS",
    "SELECTION_SOURCE_EXTS",
    "SelectionExportError",
    "export_asset_selection",
    "export_filename",
    "export_source_selection",
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

#: Sources whose GLB tree is named after the objects a re-read produces, so a selection can find
#: its way back. FEM decks are deliberately absent: their tree is elements and sets, not the
#: concept objects a CAD export would be made of, and a selection there has no honest meaning.
#: A ``.glb`` is absent because there is nothing behind it to re-read.
SELECTION_SOURCE_EXTS: frozenset[str] = frozenset({".ifc", ".step", ".stp", ".xml", ".gnx", ".sat", ".acis"})

# What a download may be called: the selection's own name where it is printable, never a path.
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class SelectionExportError(ValueError):
    """The selection cannot be exported, with the reason a user can act on."""


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
    if element is None:
        target = model
    else:
        target = find_selection(model, element, path)

    _mark_added(reparent_selection(target, out))
    return out


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
    owner = target.parent
    holder = Part(str(owner.name), placement=owner.placement.get_absolute_placement(include_rotations=True))
    holder.add_object(target)
    into.add_part(holder)
    return holder


def _step_has_solids(path: pathlib.Path) -> bool:
    from .converters.mesh_step import _step_has_solids as has_solids

    return has_solids(path)


def write_selection(asm: Any, fmt: str, out_path: pathlib.Path) -> pathlib.Path:
    """Serialise ``asm`` as ``fmt``. The same writer choices the converter makes for the format."""
    if fmt == "step":
        asm.to_stp(str(out_path))
        if not _step_has_solids(out_path):
            # Same retry as the converter's ifc->step leg: a body with no analytic AP242 form is
            # skipped by the B-rep writer, and the faceted streaming writer leaves nothing behind.
            asm.to_stp(str(out_path), writer="stream", fuse_fem=False)
        return out_path
    if fmt == "ifc":
        # The in-memory writer, never the streaming one: the streamer rebuilds the file from
        # concept objects and is only correct for a freshly built model, while a selection is
        # made of objects read from somewhere -- and is small enough not to need it.
        asm.to_ifc(str(out_path), streaming=False)
        return out_path
    raise SelectionExportError(
        f"unsupported export format {fmt!r} (expected one of {sorted(SELECTION_EXPORT_FORMATS)})"
    )


def export_filename(label: str | None, fmt: str) -> str:
    """What the download is called: the selection's name with the format's extension."""
    base = _UNSAFE_FILENAME_RE.sub("_", str(label or "").strip()).strip(" ._") or "selection"
    return f"{base[:100]}{SELECTION_EXPORT_FORMATS[fmt]}"


def selection_derived_key(
    content_token: str,
    target: dict[str, Any],
    *,
    fmt: str,
    element: str | None,
    path: Sequence[str],
    filename: str,
) -> str:
    """Where an export lands: one key per (source bytes, what was addressed, selection, format).

    ``content_token`` moves when the source's bytes do, so a re-upload is a miss rather than a
    stale file. ``target`` is what the request addressed -- a source key, or a published node's
    collection/subject/revision/node -- because one blob is commonly shared by many subjects and a
    token over the bytes alone would hand one node's export to another. The selection's PATH is
    in the token as well as its name: two same-named plates in two decks are two files.
    """
    identity = {"target": target, "format": fmt, "element": element, "path": list(path)}
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
    return write_selection(scope_to_selection(model, element, _parse_path(path)), fmt, out_path)


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
) -> pathlib.Path:
    """Read a published node through its provider's concepts, scope it, write it to ``out_path``.

    The provider decides what "the node" contains (``node`` is the loaded node, the same scope
    its build drew); ``element`` narrows within that by name, exactly as for a file.
    """
    from ada.clash.from_asset import part_for_asset_node

    part = part_for_asset_node(collection=collection, subject=subject, storage=storage, revision=revision, node=node)
    return write_selection(scope_to_selection(part, element, _parse_path(path)), fmt, out_path)
