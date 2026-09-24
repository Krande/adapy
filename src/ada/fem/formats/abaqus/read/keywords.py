"""What each keyword adapy reads accepts, per the Abaqus Keywords Guide.

Hand-curated, and deliberately only the keywords this reader acts on -- a keyword block with no entry
here still parses, it just isn't validated. The table exists so the reader can say something
useful when a deck is wrong (a required parameter missing, a parameter misspelled) instead of
mis-parsing it quietly, and so the lexer can tell a continued keyword line from a data line.

Parameter names are recorded as the guide spells them, in the guide's own categories:

``required``
    Must be present.
``one_of``
    Exactly one member of each group must be present -- ``*Solid Section`` takes ``MATERIAL``
    or ``COMPOSITE``, and the guide calls them mutually exclusive.
``optional``
    Everything else the keyword accepts. Listing these does not gate anything; it lets the
    reader tell "a parameter I know and ignore" from "a parameter that is probably a typo".
``aliases``
    Accepted spellings that mean an existing parameter, e.g. ``*Beam Section``'s ``SECT``.

Adding a keyword here is not required to read it. Start with the parameters the reader
actually consumes plus the ones a deck is likely to carry; an unlisted parameter costs a
debug line, never a wrong value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ada.config import logger

__all__ = ["KeywordSpec", "lookup", "validate", "KEYWORDS"]


@dataclass(frozen=True)
class KeywordSpec:
    name: str
    required: frozenset[str] = frozenset()
    one_of: tuple[frozenset[str], ...] = ()
    optional: frozenset[str] = frozenset()
    aliases: Mapping[str, str] = field(default_factory=dict)

    def accepts(self, name: str) -> bool:
        """Is ``name`` a parameter of this keyword (under any accepted spelling)?"""
        if name in self.required or name in self.optional or name in self.aliases:
            return True
        return any(name in group for group in self.one_of)

    def canonical(self, name: str) -> str:
        return self.aliases.get(name, name)

    def validate(self, block) -> None:
        """Log what is wrong with ``block``; never raise.

        A deck that trips these is still read as far as it can be. The reader's job is to
        import what is there, and an operator would rather have the model plus a warning
        than a traceback.
        """
        present = set(block.params)

        missing = self.required - present
        if missing:
            logger.warning(
                "abaqus read: *%s (line %d) is missing required parameter(s) %s",
                block.keyword,
                block.lineno,
                ", ".join(sorted(missing)),
            )

        for group in self.one_of:
            found = group & present
            if not found:
                logger.warning(
                    "abaqus read: *%s (line %d) needs one of %s",
                    block.keyword,
                    block.lineno,
                    " / ".join(sorted(group)),
                )
            elif len(found) > 1:
                logger.warning(
                    "abaqus read: *%s (line %d) sets mutually exclusive %s",
                    block.keyword,
                    block.lineno,
                    " and ".join(sorted(found)),
                )

        unknown = {p for p in present if not self.accepts(p)}
        if unknown:
            # Debug, not warning: the table lists what this reader knows, not everything
            # Abaqus accepts, so an unlisted parameter is far more often a gap here than a
            # mistake in the deck. It is ignored either way -- which is the whole point,
            # since the regex reader used to absorb it into the previous parameter's value.
            logger.debug(
                "abaqus read: *%s (line %d) has unrecognised parameter(s) %s -- ignored",
                block.keyword,
                block.lineno,
                ", ".join(sorted(unknown)),
            )


def _spec(name, required=(), one_of=(), optional=(), aliases=None) -> KeywordSpec:
    return KeywordSpec(
        name=name,
        required=frozenset(required),
        one_of=tuple(frozenset(g) for g in one_of),
        optional=frozenset(optional),
        aliases=dict(aliases or {}),
    )


KEYWORDS: dict[str, KeywordSpec] = {
    spec.name: spec
    for spec in (
        # ── mesh ────────────────────────────────────────────────────────────────────
        _spec("NODE", optional=("NSET", "SYSTEM", "INPUT")),
        _spec(
            "ELEMENT",
            required=("TYPE",),
            optional=("ELSET", "FILE", "INPUT", "OFFSET", "SOLID ELEMENT NUMBERING"),
        ),
        _spec("ELSET", required=("ELSET",), optional=("GENERATE", "INSTANCE", "INTERNAL", "UNSORTED")),
        _spec(
            "NSET",
            required=("NSET",),
            optional=("ELSET", "GENERATE", "INSTANCE", "INTERNAL", "SURFACE", "UNSORTED"),
        ),
        # ── sections ────────────────────────────────────────────────────────────────
        _spec(
            "SOLID SECTION",
            required=("ELSET",),
            one_of=(("MATERIAL", "COMPOSITE"),),
            optional=("CONTROLS", "LAYUP", "ORDER", "ORIENTATION", "REF NODE", "STACK DIRECTION", "SYMMETRIC"),
        ),
        _spec(
            "SHELL SECTION",
            required=("ELSET",),
            one_of=(("MATERIAL", "COMPOSITE"),),
            optional=(
                "CONTROLS",
                "DENSITY",
                "LAYUP",
                "NODAL THICKNESS",
                "OFFSET",
                "ORIENTATION",
                "POISSON",
                "SECTION INTEGRATION",
                "SHELL THICKNESS",
                "STACK DIRECTION",
            ),
        ),
        _spec(
            "BEAM SECTION",
            required=("ELSET", "MATERIAL", "SECTION"),
            optional=("CONTROLS", "LUMPED", "POISSON", "ROTARY INERTIA", "TEMPERATURE", "ORIENTATION", "DENSITY"),
            aliases={"SECT": "SECTION"},
        ),
        _spec(
            "CONNECTOR SECTION",
            required=("ELSET",),
            optional=("BEHAVIOR", "CONTROLS", "ELIMINATION"),
            aliases={"BEHAVIOUR": "BEHAVIOR"},
        ),
        _spec(
            "CONNECTOR BEHAVIOR", required=("NAME",), optional=("EXTRAPOLATION", "INTEGRATION", "REGULARIZE", "RTOL")
        ),
        _spec(
            "CONNECTOR ELASTICITY",
            optional=(
                "COMPONENT",
                "DEPENDENCIES",
                "EXTRAPOLATION",
                "FREQUENCY DEPENDENCE",
                "INDEPENDENT COMPONENTS",
                "NONLINEAR",
                "REGULARIZE",
                "RIGID",
                "RTOL",
                "UNSYMM",
            ),
        ),
        # ── masses ──────────────────────────────────────────────────────────────────
        _spec("MASS", required=("ELSET",), optional=("ALPHA", "COMPOSITE", "ORIENTATION", "TYPE", "UNITS")),
        _spec("ROTARY INERTIA", required=("ELSET",), optional=("ALPHA", "COMPOSITE", "ORIENTATION", "UNITS")),
        _spec("NONSTRUCTURAL MASS", required=("ELSET",), optional=("DISTRIBUTION", "UNITS")),
        # ── materials ───────────────────────────────────────────────────────────────
        _spec(
            "MATERIAL",
            required=("NAME",),
            optional=("RTOL", "SRATE FACTOR", "SRATE TIME", "STRAIN RATE INTERPOLATION", "USER TYPE"),
        ),
        _spec("DENSITY", optional=("DEPENDENCIES", "PORE FLUID", "SLURRY")),
        _spec("ELASTIC", optional=("COHESIVE OFFSET", "COMPRESSION FACTOR", "DEPENDENCIES", "MODULI", "TYPE")),
        _spec(
            "PLASTIC",
            optional=(
                "DATA TYPE",
                "DEPENDENCIES",
                "EXTRAPOLATION",
                "HARDENING",
                "NUMBER BACKSTRESSES",
                "PROPERTIES",
                "RATE",
                "SCALESTRESS",
                "STATIC RECOVERY",
            ),
        ),
        _spec(
            "EXPANSION",
            optional=(
                "DEFINITION",
                "DEPENDENCIES",
                "FIELD",
                "LIQUID",
                "PORE FLUID",
                "PROPERTIES",
                "TYPE",
                "USER",
                "ZERO",
            ),
        ),
        # ── constraints and surfaces ────────────────────────────────────────────────
        _spec(
            "SURFACE",
            required=("NAME",),
            optional=(
                "COMBINE",
                "CROP",
                "DEFINITION",
                "FILLET RADIUS",
                "INTERNAL",
                "MAX RATIO",
                "NO OFFSET",
                "NO THICK",
                "PROPERTY",
                "REGION TYPE",
                "SCALE THICK",
                "TRIM",
                "TYPE",
            ),
        ),
        _spec("SURFACE SMOOTHING", required=("NAME",)),
        _spec(
            "TIE",
            required=("NAME",),
            optional=(
                "ADJUST",
                "CONSTRAINT RATIO",
                "CYCLIC SYMMETRY",
                "NO ROTATION",
                "NO THICKNESS",
                "POSITION TOLERANCE",
                "TIED NSET",
                "TYPE",
            ),
        ),
        _spec(
            "COUPLING",
            required=("CONSTRAINT NAME", "REF NODE", "SURFACE"),
            optional=("INFLUENCE RADIUS", "ORIENTATION"),
        ),
        _spec("KINEMATIC", optional=("ALPHA",)),
        _spec(
            "SHELL TO SOLID COUPLING",
            required=("CONSTRAINT NAME",),
            optional=("INFLUENCE DISTANCE", "POSITION TOLERANCE"),
        ),
        _spec(
            "RIGID BODY",
            optional=(
                "ANALYTICAL SURFACE",
                "ELSET",
                "PIN NSET",
                "REF NODE",
                "TIE NSET",
                "ALPHA",
                "DENSITY",
                "ISOTHERMAL",
                "NODAL THICKNESS",
                "OFFSET",
                "POSITION",
            ),
        ),
        _spec("MPC", optional=("ALPHA", "INPUT", "MODE", "USER")),
        # ── interactions ────────────────────────────────────────────────────────────
        _spec(
            "SURFACE INTERACTION",
            required=("NAME",),
            optional=("DEPVAR", "PAD THICKNESS", "PROPERTIES", "USER", "UNSYMM"),
        ),
        _spec("FRICTION", optional=("ANISOTROPIC BEHAVIOR", "DEPENDENCIES", "DEPVAR", "PROPERTIES", "TAUMAX")),
        _spec("SURFACE BEHAVIOR", optional=("NO SEPARATION", "PRESSURE-OVERCLOSURE", "PENALTY")),
        _spec(
            "CONTACT PAIR",
            required=("INTERACTION",),
            optional=(
                "ADJUST",
                "CPSET",
                "GEOMETRIC CORRECTION",
                "HCRIT",
                "MECHANICAL CONSTRAINT",
                "NO THICKNESS",
                "OP",
                "POSITION TOLERANCE",
                "SMALL SLIDING",
                "SMOOTH",
                "TIED",
                "TRACKING",
                "TYPE",
                "WEIGHT",
            ),
        ),
        _spec("CONTACT", optional=("OP",)),
        _spec("CONTACT INCLUSIONS", optional=("ALL EXTERIOR",)),
        _spec("CONTACT PROPERTY ASSIGNMENT"),
        _spec("CONTACT FORMULATION", optional=("TYPE",)),
        _spec("CONTACT INITIALIZATION ASSIGNMENT"),
        _spec("SURFACE PROPERTY ASSIGNMENT", optional=("PROPERTY",)),
        # ── model structure ─────────────────────────────────────────────────────────
        _spec("PART", required=("NAME",)),
        _spec("INSTANCE", optional=("NAME", "PART", "INSTANCE", "LIBRARY")),
        _spec("ASSEMBLY", required=("NAME",)),
        _spec("STEP", optional=("AMPLITUDE", "EXTRAPOLATION", "INC", "NAME", "NLGEOM", "PERTURBATION", "UNSYMM")),
        _spec("INCLUDE", required=("INPUT",), optional=("PASSWORD",)),
        _spec(
            "ORIENTATION",
            required=("NAME",),
            optional=("DEFINITION", "DISPERSION", "LOCAL DIRECTIONS", "SYSTEM"),
        ),
        _spec(
            "INITIAL CONDITIONS",
            required=("TYPE",),
            optional=("CRITERION", "DEFINITION", "FILE", "GEOSTATIC", "INC", "INPUT", "INTERPOLATE"),
        ),
    )
}


def lookup(keyword: str) -> KeywordSpec | None:
    """The spec for ``keyword`` (already normalized), or ``None`` when it isn't registered."""
    return KEYWORDS.get(keyword)


#: Keywords that change nothing in the model: the title, the preprocessor's own printout, and
#: every output, print, file and restart request. When the reader meets one it does not read, it
#: is reported as a ``note``, not an ``omitted`` -- nothing of the model is missing without it.
#: Taken from the Abaqus Keywords Guide's output/print/file/restart keywords, names only.
NO_MODEL_EFFECT = frozenset(
    {
        "HEADING",
        "PREPRINT",
        "PRINT",
        "OUTPUT",
        "POST OUTPUT",
        "FILE FORMAT",
        "FILE OUTPUT",
        "RESTART",
        "MONITOR",
        "NODE OUTPUT",
        "NODE PRINT",
        "NODE FILE",
        "ELEMENT OUTPUT",
        "EL PRINT",
        "EL FILE",
        "CONTACT OUTPUT",
        "CONTACT PRINT",
        "CONTACT FILE",
        "ENERGY OUTPUT",
        "ENERGY PRINT",
        "ENERGY FILE",
        "MODAL OUTPUT",
        "MODAL PRINT",
        "MODAL FILE",
        "RADIATION OUTPUT",
        "RADIATION PRINT",
        "RADIATION FILE",
        "SECTION PRINT",
        "SECTION FILE",
        "TORQUE PRINT",
        "INTEGRATED OUTPUT",
        "INTEGRATED OUTPUT SECTION",
        "INCREMENTATION OUTPUT",
        "MATRIX OUTPUT",
        "ELEMENT MATRIX OUTPUT",
        "ELEMENT OPERATOR OUTPUT",
        "OPERATOR OUTPUT",
        "SUBSTRUCTURE MATRIX OUTPUT",
        "SUBSTRUCTURE OUTPUT",
        "VIEW FACTOR OUTPUT",
        "USER OUTPUT VARIABLES",
        "ELEMENT USER OUTPUT VARIABLES",
    }
)

#: Solver and diagnostic settings: how Abaqus checks, adjusts or reports while it solves, with
#: nothing of the model in them. Reported as a ``note`` for the same reason as the above.
#: Deliberately NOT here: *Section Controls and *Damping Controls, which change how elements
#: behave -- skipping those does lose something, so they stay ``omitted``.
SOLVER_CONTROLS = frozenset({"CONSTRAINT CONTROLS", "CONTROLS", "SOLVER CONTROLS", "DIAGNOSTICS"})


def validate(block) -> None:
    """Validate ``block`` against its spec, if it has one."""
    spec = KEYWORDS.get(block.keyword)
    if spec is not None:
        spec.validate(block)
