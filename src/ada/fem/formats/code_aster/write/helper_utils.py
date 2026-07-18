from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def resolve_ids_in_multiple(tags, tags_data, is_elem):
    """Find elements shared by multiple sets.

    Hot path on a jacket FEM model → Code_Aster (.med) conversion
    (~530 s of a ~660 s job before tuning). Three O(N²) issues in
    the original loop:

      1. ``names not in tags.values()`` — linear scan over every
         tag's name-list per element. ``rmap`` already exists for
         O(1) tuple lookup; just use it.
      2. ``min(tags.keys()) / max(tags.keys())`` recomputed inside
         the loop — pulls a fresh aggregate every time a new
         multi-set tag is allocated. Track the running min/max
         incrementally.
      3. ``mem not in fin_data[new_int]`` — linear list-membership
         check. Maintain a parallel id-set for O(1) tests.
    """
    from ada.fem import FemSet

    rmap = {tuple(v): r for r, v in tags.items()}
    # Incremental min / max so we don't aggregate ``tags.keys()``
    # on every new-tag allocation. ``min`` / ``max`` over an empty
    # dict would error; the loop only allocates after a successful
    # lookup miss, and a miss implies at least one existing entry
    # — but be defensive in case ``tags`` starts non-empty either
    # way (or empty in the elem branch — the original code would
    # have crashed in that case too).
    next_neg = (min(tags.keys()) - 1) if tags else -1
    next_pos = (max(tags.keys()) + 1) if tags else 1
    fin_data: dict = {}
    # Parallel ``id()``-set per output bucket so the
    # ``mem not in fin_data[new_int]`` check becomes O(1) regardless
    # of how many members already accumulated. ids are stable for
    # the lifetime of the list (which we hold).
    seen_ids: dict[int, set[int]] = {}
    for t, memb in tags_data.items():
        fin_data[t] = []
        seen_ids[t] = set()
        for mem in memb:
            refs = [x for x in mem.refs if type(x) is FemSet]
            if len(refs) > 1:
                names = [r.name for r in refs]
                key = tuple(names)
                new_int = rmap.get(key)
                if new_int is None:
                    new_int = next_neg if is_elem else next_pos
                    if is_elem:
                        next_neg -= 1
                    else:
                        next_pos += 1
                    tags[new_int] = names
                    rmap[key] = new_int
                    fin_data[new_int] = []
                    seen_ids[new_int] = set()
                # Value-based dedup key (member id) rather than id(mem): array-backed
                # proxies are minted per access, so object identity isn't stable; the
                # entity id is unique within a node/element set context either way.
                rid = int(mem.id)
                bucket_ids = seen_ids[new_int]
                if rid not in bucket_ids:
                    fin_data[new_int].append(mem)
                    bucket_ids.add(rid)
            else:
                fin_data[t].append(mem)
                seen_ids[t].add(int(mem.id))
    to_be_removed = [i for i, f in fin_data.items() if len(f) == 0]
    for t in to_be_removed:
        fin_data.pop(t)
        tags.pop(t)
    return fin_data
