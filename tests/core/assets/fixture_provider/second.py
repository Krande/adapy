r"""A SECOND test-only provider with a different private format.

Its source is an indented outline, not JSON at all -- deliberately nothing like the first
fixture's newline-delimited records. It exists to prove one property: a single collection may be
MIXED, with one branch produced by a provider whose source is one format and a sibling branch by
a provider using another, each carrying its own build capability, inside one hierarchy the
browser renders as one tree.

Core learns neither format. Both are translated into core's schemas at publish time, and the
layering gate greps src/ada for the tokens of both.
"""

from __future__ import annotations

import hashlib

from ada.assets.keys import asset_key, revision_from_instant
from ada.assets.manifest import (
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)

SECOND_PROVIDER_ID = "fixture-outline"
SECOND_SOURCE_FILENAME = "source.outline"
SECOND_BUILD_CAPABILITY = "asset-build-outline"

# The vendor's own format: two-space indentation, "name | category". No JSON, no shared vocabulary
# with the other fixture.
SECOND_SOURCE_TEXT = """\
unit-3 | unit
  valve-x | equipment
  valve-y | equipment
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_outline(raw: bytes) -> list[dict]:
    """The ONLY reader of this format, and it lives in tests/."""
    out: list[dict] = []
    stack: list[str] = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        depth = (len(line) - len(line.lstrip(" "))) // 2
        name, _, category = line.strip().partition(" | ")
        del stack[depth:]
        out.append({"ref": name, "up": stack[-1] if stack else None, "cat": category or "node"})
        stack.append(name)
    return out


def publish_second_branch(
    store,
    *,
    collection: str = "fixture-a",
    instant: str = "2026-09-21T14:30:01Z",
    attach_to: str = "site",
) -> str:
    """Publish this provider's branch into an EXISTING collection, hanging off ``attach_to``.

    Writes only its own subjects. The collection hierarchy is rewritten by the caller, which is
    what a real mixed publish does too: the spine is the collection's, the branches are not.
    """
    revision = revision_from_instant(instant)
    raw = SECOND_SOURCE_TEXT.encode("utf-8")
    source_key = asset_key(collection, SECOND_PROVIDER_ID, revision, SECOND_SOURCE_FILENAME)
    store.put(source_key, raw)

    records = parse_outline(raw)
    parents = {r["up"] for r in records if r["up"]}
    nodes = []
    for rec in records:
        ref = rec["ref"]
        is_leaf = ref not in parents
        nodes.append(
            {
                "id": ref,
                "parent": rec["up"] or attach_to,
                "label": ref.replace("-", " ").title(),
                "kind": rec["cat"],
                "leaf": is_leaf,
                "delivery": "build" if is_leaf else "",
                "provider": SECOND_PROVIDER_ID,
            }
        )
        manifest = AssetManifest(
            provider=SECOND_PROVIDER_ID,
            collection=collection,
            subject=ref,
            revision=revision,
            node=ref,
            produced_at=instant,
            published_at=instant,
            delivery="build" if is_leaf else "none",
            build=(
                BuildSpec(
                    capability=SECOND_BUILD_CAPABILITY,
                    options={"outline_ref": ref, "source_key": source_key},
                    fingerprint_inputs=("source_key", "outline_ref"),
                )
                if is_leaf
                else None
            ),
            artefacts=(ArtefactEntry(role="source", key=source_key, sha256=_sha(raw), size=len(raw)),),
            counts={"nodes": 1},
        )
        store.put(asset_key(collection, ref, revision, MANIFEST_FILENAME), manifest.to_json())
    return revision


def second_branch_nodes(attach_to: str = "site") -> list[dict]:
    """The rows this provider contributes to the collection spine."""
    records = parse_outline(SECOND_SOURCE_TEXT.encode("utf-8"))
    parents = {r["up"] for r in records if r["up"]}
    return [
        {
            "id": r["ref"],
            "parent": r["up"] or attach_to,
            "label": r["ref"].replace("-", " ").title(),
            "kind": r["cat"],
            "leaf": r["ref"] not in parents,
            "delivery": "build" if r["ref"] not in parents else "",
            "provider": SECOND_PROVIDER_ID,
        }
        for r in records
    ]
