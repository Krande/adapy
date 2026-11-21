"""Per-element associations: clash contacts, connections, welds, simulation subjects and runs,
filed under the model elements they are about.

A hierarchy level and everything beneath it each carry their own links -- the contacts a clash run
found on them, the connections and welds a detail run built for them, the subjects made of those
connections and the simulation runs on them -- so "what has been found, built and simulated under
this node" is one query, whichever document produced each answer.

* :mod:`.keys` -- element keys (``node:...``, ``file:...#g:``, ``file:...#p:``) and the connection
  key ``ck``;
* :mod:`.records` -- the rows, their stale / orphan / superseded flags and their documents;
* :mod:`.resolve` -- a GLB element id to its key;
* :mod:`.fingerprints` -- per-element fingerprints (numpy);
* :mod:`.rows` -- the rows each producing document gives;
* :mod:`.store` -- where they are kept (``LinkStore``: files always, Postgres when the deployment has one).

The producing documents stay the truth: every row can be made again from them (reindex).
"""

from .keys import (
    ElementKey,
    connection_key,
    file_key,
    is_element_key,
    key_ancestors,
    node_key,
    normalise_path,
    normalise_segment,
    parse_key,
)
from .records import KINDS, ElementState, LinkRow, SourceState, flag_rows, group_rows

__all__ = [
    "KINDS",
    "ElementKey",
    "ElementState",
    "LinkRow",
    "SourceState",
    "connection_key",
    "file_key",
    "flag_rows",
    "group_rows",
    "is_element_key",
    "key_ancestors",
    "node_key",
    "normalise_path",
    "normalise_segment",
    "parse_key",
]
