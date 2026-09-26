"""CAE name rules, and why a collision has to be caught here rather than by the kernel.

Measured against a real Abaqus 2025 kernel (``CAE_PROBED_FACTS`` §3), not recalled:
there is no 38- or 80-character ceiling, and spaces, dashes, slashes and non-ASCII
letters are all accepted::

    38 chars  OK     "has space" OK     "PL_1/2" OK     "aeø" OK
    80 chars  OK     "has-dash"  OK     "has.dot" FAIL -> invalid name

So the one character that has to be sanitised is the **dot**, which adapy and GeniE
names carry routinely (profile revisions, ``model.v2``-style names).

Probed separately for this writer: **a duplicate name does not raise.**
``m.Part(name='G', ...)`` on a name that already exists silently *replaces* the part
and invalidates every handle to the old one — the probe's next line died with
``AccessError: mdb.models['Model-1'].parts['G'] no longer exists``. The same is true
of ``p.Set``, and a replaced set is worse than a replaced part: the section
assignment that referenced it keeps working, now pointing at a different region. A
collision therefore cannot be delegated to the kernel; it has to fail before the
script is written.
"""

from __future__ import annotations

import json
import pathlib

#: Characters CAE rejects in an object name, mapped to their replacement.
#: Only the dot is known to be rejected (probed); keeping the table narrow means a
#: name that needs no change is emitted byte-for-byte as adapy holds it.
CAE_NAME_REPLACEMENTS = {".": "_"}


class CaeNameError(ValueError):
    """A name cannot be expressed in CAE, or two names would collide there."""


def sanitise_cae_name(original: str) -> str:
    """``original`` with every CAE-illegal character replaced.

    Raises for a name that has nothing left to work with, because an empty CAE
    name is rejected and a blank one is a symptom, not something to paper over.
    """
    if original is None:
        raise CaeNameError("cannot build a CAE name from None")
    name = str(original)
    for bad, good in CAE_NAME_REPLACEMENTS.items():
        name = name.replace(bad, good)
    if name.strip() == "":
        raise CaeNameError("cannot build a CAE name from {0!r}: it is blank".format(original))
    return name


class NameRegistry:
    """One CAE naming scope: a model's parts, its materials, its profiles, its sections.

    Scopes are separate because CAE's repositories are — ``m.parts``, ``m.materials``,
    ``m.profiles`` and ``m.sections`` are independent, so a profile and a section may
    share a name while two parts may not. (Sets are per-part in CAE, but the writer
    keeps one scope for them anyway; see ``_SET_SCOPE`` in ``writer`` for why.)
    """

    def __init__(self, scope: str):
        self.scope = scope
        self._by_cae: dict[str, str] = {}
        self._changed: dict[str, str] = {}

    def _record(self, cae: str, original: str) -> str:
        self._by_cae[cae] = original
        if cae != original:
            self._changed[cae] = original
        return cae

    def allocate_unique(self, original: str) -> str:
        """Claim a name no other object may have. Raises on any repeat.

        Used for parts and for per-beam sets, where a second object under the same
        name does not add anything — it silently takes the first one's place.
        """
        cae = sanitise_cae_name(original)
        if cae in self._by_cae:
            previous = self._by_cae[cae]
            if previous == original:
                raise CaeNameError(
                    "two objects in CAE scope {0!r} are both named {1!r}; CAE would silently "
                    "replace the first with the second".format(self.scope, original)
                )
            raise CaeNameError(
                "CAE name collision in scope {0!r}: {1!r} and {2!r} both sanitise to {3!r}; "
                "CAE would silently replace the first with the second".format(self.scope, previous, original, cae)
            )
        return self._record(cae, original)

    def allocate_shared(self, original: str) -> str:
        """Claim a name for an object many others may reference (a material, a profile).

        Idempotent for the same original — asking twice for ``S355`` is one material.
        Two *different* originals landing on one CAE name is still a collision.
        """
        cae = sanitise_cae_name(original)
        previous = self._by_cae.get(cae)
        if previous is not None and previous != original:
            raise CaeNameError(
                "CAE name collision in scope {0!r}: {1!r} and {2!r} both sanitise to {3!r}; "
                "CAE would silently replace the first with the second".format(self.scope, previous, original, cae)
            )
        return self._record(cae, original)

    @property
    def changed(self) -> dict[str, str]:
        """CAE name -> original, for the names sanitisation actually altered."""
        return dict(self._changed)


def dump_name_map(registries: dict[str, NameRegistry], path: pathlib.Path) -> bool:
    """Write ``<stem>.name_map.json`` if any name changed; return whether it did.

    Direction and shape follow the code_aster precedent
    (:mod:`ada.fem.formats.code_aster.write.name_map`): the emitted name is the key,
    because that is what a CAE result carries, and the original is what the user is
    trying to get back to. Only altered names are recorded — a pass-through name
    needs no entry.
    """
    changed = {scope: registries[scope].changed for scope in sorted(registries)}
    changed = {scope: mapping for scope, mapping in changed.items() if mapping}
    if not changed:
        return False
    pathlib.Path(path).write_text(json.dumps(changed, indent=2, sort_keys=True), encoding="utf-8")
    return True


__all__ = [
    "CAE_NAME_REPLACEMENTS",
    "CaeNameError",
    "NameRegistry",
    "dump_name_map",
    "sanitise_cae_name",
]
