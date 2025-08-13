from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def resolve_ids_in_multiple(tags, tags_data, is_elem):
    """Find elements shared by multiple sets"""
    from ada.fem import FemSet

    rmap = {tuple(v): r for r, v in tags.items()}
    fin_data = dict()
    for t, memb in tags_data.items():
        fin_data[t] = []
        for mem in memb:
            refs = list(filter(lambda x: type(x) is FemSet, mem.refs))
            if len(refs) > 1:
                names = [r.name for r in refs]
                if names not in tags.values():
                    new_int = min(tags.keys()) - 1 if is_elem else max(tags.keys()) + 1
                    tags[new_int] = names
                    rmap[tuple(names)] = new_int
                    fin_data[new_int] = []
                else:
                    new_int = rmap[tuple(names)]
                if mem not in fin_data[new_int]:
                    fin_data[new_int].append(mem)
            else:
                fin_data[t].append(mem)
    to_be_removed = []
    for i, f in fin_data.items():
        if len(f) == 0:
            to_be_removed.append(i)
    for t in to_be_removed:
        fin_data.pop(t)
        tags.pop(t)
    return fin_data
