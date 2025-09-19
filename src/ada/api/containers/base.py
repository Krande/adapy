from __future__ import annotations

from bisect import insort
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ada import Part

from collections.abc import MutableSequence
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")
K = TypeVar("K")  # for generic ID
N = TypeVar("N", bound=int)  # numeric ID


class IndexedCollection(MutableSequence[T], Generic[T, K, N]):
    def __init__(
        self,
        items: Iterable[T] = (),
        *,
        sort_key: Callable[[T], Any],
        id_key: Callable[[T], K],
        name_key: Optional[Callable[[T], str]] = None,
        numeric_id_key: Optional[Callable[[T], N]] = None,
    ):
        self._sort_key = sort_key
        self._id_key = id_key
        self._name_key = name_key
        self._numeric_id_key = numeric_id_key

        self._items = sorted(items, key=sort_key)
        # always build the primary id map
        self._idmap = {id_key(i): i for i in self._items}
        # build a name‐map if requested
        if name_key:
            self._nmap = {name_key(i): i for i in self._items}
        # build a numeric‐id map if requested
        if numeric_id_key:
            self._num_map = {numeric_id_key(i): i for i in self._items}

    # — all your MutableSequence methods here —
    # __len__, __getitem__, __delitem__, __setitem__, insert
    # --- MutableSequence methods ---
    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __delitem__(self, i):
        item = self._items.pop(i)
        self._idmap.pop(self._id_key(item), None)
        self._nmap.pop(self._name_key(item), None)

    def __setitem__(self, i, item: T):
        # replace at index i
        del self[i]
        self.insert(i, item)

    def insert(self, i: int, item: T) -> None:
        # enforce uniqueness by name or id if you like
        _id = self._id_key(item)
        _name = self._name_key(item)
        if _id in self._idmap or _name in self._nmap:
            raise ValueError(f"Duplicate {_name=} or {_id=}")
        insort(self._items, item, key=self._sort_key)
        self._idmap[_id] = item
        self._nmap[_name] = item

    def add(self, item: T) -> None:
        self.insert(0, item)

    def __contains__(self, item: T) -> bool:
        return self._id_key(item) in self._idmap

    def from_id(self, val: K) -> Optional[T]:
        return self._idmap.get(val)

    def from_name(self, name: str) -> Optional[T]:
        return getattr(self, "_nmap", {}).get(name)

    def from_numeric_id(self, num: int) -> Optional[T]:
        return getattr(self, "_num_map", {}).get(num)

    @property
    def items(self) -> list[T]:
        return self._items


class BaseCollections:
    """The Base class for all collections"""

    def __init__(self, parent: Part):
        self._parent = parent

    @property
    def parent(self) -> Part:
        return self._parent


class NumericMapped(BaseCollections):
    def __init__(self, parent):
        super(NumericMapped, self).__init__(parent=parent)
        self._name_map = dict()
        self._id_map = dict()

    def recreate_name_and_id_maps(self, collection):
        self._name_map = {n.name: n for n in collection}
        self._id_map = {n.id: n for n in collection}
        # Invalidate max_id cache when maps are recreated
        if hasattr(self, "_max_id"):
            self._max_id = None

    @property
    def max_id(self):
        if len(self._id_map.keys()) == 0:
            return 0
        # Use cached value if available, otherwise calculate and cache
        if hasattr(self, "_max_id") and self._max_id is not None:
            return self._max_id
        self._max_id = max(self._id_map.keys())
        return self._max_id
