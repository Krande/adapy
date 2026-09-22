"""Which formats ``ada convert`` reads and writes, and how each one lands on disk.

Standard library only, and deliberately so: ``ada_cli`` builds its parser without
importing ``ada`` (see ``ada_cli.main``'s docstring), and the parser needs these names at
parse time for ``choices=``. Importing ``ada`` here would pull the whole CAD/FEM surface
into ``ada --help``.

That independence is the reason for :mod:`tests.core.test_cli_formats_contract`: the FEM
entries below duplicate what ``ada.fem.formats.general.get_fem_imports`` /
``get_fem_exports`` know, and the contract test fails if the two ever disagree.

Two extensions name more than one FEM format -- ``.inp`` is Abaqus and Calculix, ``.fem``
is Sesam and USFOS -- so each extension has exactly one *default owner* here, chosen to
match what ``ada.fem.formats.general.interpret_fem_format_from_path`` already does, and
the minority dialect is reached with ``--to``.
"""

from __future__ import annotations

import pathlib

#: ``--from`` name -> the extensions that imply it.
READ_FORMATS: dict[str, tuple[str, ...]] = {
    "ifc": ("ifc",),
    "step": ("step", "stp"),
    "xml": ("xml",),
    "acis": ("sat", "acis"),
    "abaqus": ("inp",),
    "sesam": ("fem", "sif"),
    "code_aster": ("med", "rmed"),
}

#: ``--to`` name -> the extensions that imply it.
WRITE_FORMATS: dict[str, tuple[str, ...]] = {
    "ifc": ("ifc",),
    "step": ("step", "stp"),
    "gltf": ("gltf", "glb"),
    "xml": ("xml",),
    "abaqus": ("inp",),
    "calculix": ("inp",),
    "sesam": ("fem",),
    "usfos": ("fem",),
    "code_aster": ("med",),
}

#: The FEM formats, i.e. the ones that go through ``Assembly.to_fem`` / ``ada.from_fem``
#: rather than a direct writer. Pinned against the library's own maps by the contract test.
FEM_READ_FORMATS: tuple[str, ...] = ("abaqus", "sesam", "code_aster")
FEM_WRITE_FORMATS: tuple[str, ...] = ("abaqus", "calculix", "code_aster", "sesam", "usfos")

#: The file each FEM writer leaves in ``<scratch>/<name>/`` that *is* the deck. Everything
#: else a writer produces is a sidecar and is moved next to the output the user named.
#: USFOS ignores the name it is given and always writes ``ufo_bulk.fem``.
FEM_WRITE_PRIMARY: dict[str, str] = {
    "abaqus": "{name}.inp",
    "calculix": "{name}.inp",
    "sesam": "{name}T1.FEM",
    "usfos": "ufo_bulk.fem",
    "code_aster": "{name}.med",
}

#: Extensions that more than one write format claims, and who claims them.
SHARED_WRITE_EXT: dict[str, tuple[str, ...]] = {
    "inp": ("abaqus", "calculix"),
    "fem": ("sesam", "usfos"),
}

#: extension -> the format used when no flag is given.
DEFAULT_READ_BY_EXT: dict[str, str] = {
    "ifc": "ifc",
    "step": "step",
    "stp": "step",
    "xml": "xml",
    "sat": "acis",
    "acis": "acis",
    "inp": "abaqus",
    "fem": "sesam",
    "sif": "sesam",
    "med": "code_aster",
    "rmed": "code_aster",
}

DEFAULT_WRITE_BY_EXT: dict[str, str] = {
    "ifc": "ifc",
    "step": "step",
    "stp": "step",
    "gltf": "gltf",
    "glb": "gltf",
    "xml": "xml",
    "inp": "abaqus",
    "fem": "sesam",
    "med": "code_aster",
}


def suffix(path) -> str:
    """The extension of ``path``, lowercase and without its dot."""
    return pathlib.Path(path).suffix.lstrip(".").lower()


def infer_read_format(path) -> str | None:
    """The input format ``path``'s extension implies, or ``None`` if it implies none."""
    return DEFAULT_READ_BY_EXT.get(suffix(path))


def infer_write_format(path) -> str | None:
    """The output format ``path``'s extension implies, or ``None`` if it implies none."""
    return DEFAULT_WRITE_BY_EXT.get(suffix(path))


def format_table_text() -> str:
    """What ``ada convert --list-formats`` prints.

    ASCII only and built from the tables above, so it is the same answer the ``choices=`` are,
    and it cannot fail to encode on a console that is not UTF-8.
    """
    lines = ["Input formats (--from):", ""]
    for name, exts in READ_FORMATS.items():
        lines.append(f"  {name:<12} {', '.join('.' + e for e in exts)}")
    lines += [
        "",
        "There is no --from calculix: a Calculix deck is an Abaqus deck to the reader, so",
        "read one with --from abaqus. Calculix .frd results are not read at all -- they are",
        "results, not a model. A .rmed IS read, as a mesh; its result fields are ignored.",
        "",
        "Output formats (--to):",
        "",
    ]
    for name, exts in WRITE_FORMATS.items():
        note = ""
        if name in FEM_WRITE_PRIMARY:
            primary = FEM_WRITE_PRIMARY[name].format(name="<name>")
            note = f"  writes {primary}"
            if name == "code_aster":
                note += " plus .comm and two .json sidecars"
            elif name == "sesam":
                note += ", plus sestra.inp when the model has a step"
        lines.append(f"  {name:<12} {', '.join('.' + e for e in exts):<14}{note}".rstrip())
    lines += [
        "",
        "Two output extensions are claimed by more than one FEM format. Each has one default",
        "owner, matching what the library's own path-to-format inference does; the minority",
        "dialect is reached with --to:",
        "",
    ]
    for ext, owners in SHARED_WRITE_EXT.items():
        default = DEFAULT_WRITE_BY_EXT[ext]
        others = [o for o in owners if o != default]
        flags = " / ".join(f"--to {o}" for o in others)
        lines.append(f"  .{ext:<5} -> {default} by default; {flags} for the other{'s' if len(others) > 1 else ''}")
    lines += [
        "",
        "The output path is the file you name. Extra files a format needs are written",
        "beside it, and every path written is printed.",
    ]
    return "\n".join(lines) + "\n"
