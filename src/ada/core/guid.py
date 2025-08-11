# IfcOpenShell - IFC toolkit and geometry engine
# Copyright (C) 2021 Thomas Krijnen <thomas@aecgeeks.com>
#
# This file is part of IfcOpenShell.
#
# IfcOpenShell is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcOpenShell is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcOpenShell.  If not, see <http://www.gnu.org/licenses/>.

"""Reads and writes encoded GlobalIds"""

from __future__ import absolute_import, annotations, division, print_function

import hashlib
import string
import threading
import uuid
from collections import deque
from functools import reduce

from ada.config import Config

chars = string.digits + string.ascii_uppercase + string.ascii_lowercase + "_$"


def compress(g):
    """Optimized version of compress function"""
    bs = [int(g[i : i + 2], 16) for i in range(0, len(g), 2)]

    # Pre-calculate the result size and use a list for string building
    result = [""] * 22

    # First 2 characters
    v = bs[0]
    result[0] = chars[v // 64]
    result[1] = chars[v % 64]

    # Remaining characters in groups of 4
    for i in range(1, 16, 3):
        v = (bs[i] << 16) + (bs[i + 1] << 8) + bs[i + 2]
        idx = 2 + (i - 1) // 3 * 4
        result[idx] = chars[(v >> 18) & 63]
        result[idx + 1] = chars[(v >> 12) & 63]
        result[idx + 2] = chars[(v >> 6) & 63]
        result[idx + 3] = chars[v & 63]

    return "".join(result)


def expand(g):
    def b64(v):
        return reduce(lambda a, b: a * 64 + b, map(lambda c: chars.index(c), v))

    bs = [b64(g[0:2])]
    for i in range(5):
        d = b64(g[2 + 4 * i : 6 + 4 * i])
        bs += [(d >> (8 * (2 - j))) % 256 for j in range(3)]
    return "".join(["%02x" % b for b in bs])


def split(g):
    return "{%s-%s-%s-%s-%s}" % (g[:8], g[8:12], g[12:16], g[16:20], g[20:])


def new():
    return compress(uuid.uuid4().hex)


# Add these variables for the cache
_guid_cache = deque()
_guid_cache_lock = threading.Lock()
_cache_size = Config().general_guid_cache_num  # Default cache size
_cache_refill_threshold = Config().general_guid_cache_refill_threshold  # When to refill the cache
_guid_cache_enabled = Config().general_guid_cache_enabled


def fill_guid_cache(count=None, name=None):
    """
    Fill the GUID cache with a specified number of GUIDs.
    If count is None, fills to the default cache size.
    If name is provided, creates GUIDs based on that name with incrementing counters.
    """
    count = count or _cache_size
    with _guid_cache_lock:
        if name is None:
            # Generate random GUIDs
            for _ in range(count):
                _guid_cache.append(compress(uuid.uuid4().hex))
        else:
            # Generate deterministic GUIDs based on name with counter
            base_name = name.encode() if not isinstance(name, bytes) else name
            start_idx = len(_guid_cache)
            for i in range(count):
                # Append counter to make each GUID unique
                n = base_name + str(start_idx + i).encode()
                hexdig = hashlib.md5(n).hexdigest()
                _guid_cache.append(compress(hexdig))


def get_guid(name=None):
    """
    Get a GUID from the cache if available, or generate a new one.
    If name is provided, generates a deterministic GUID based on the name.
    """
    if name is not None:
        # For named GUIDs, always generate directly (not cached)
        if not isinstance(name, bytes):
            n = name.encode()
        else:
            n = name
        hexdig = hashlib.md5(n).hexdigest()
        return compress(hexdig)

    if _guid_cache_enabled is False:
        return compress(uuid.uuid4().hex)

    with _guid_cache_lock:
        # Check if we need to refill the cache
        if len(_guid_cache) <= _cache_refill_threshold:
            # Refill in a separate thread to avoid blocking
            refill_thread = threading.Thread(target=fill_guid_cache, args=(_cache_size - len(_guid_cache),))
            refill_thread.daemon = True
            refill_thread.start()

        # Return a GUID from the cache if available
        if _guid_cache:
            return _guid_cache.popleft()

    # If cache is empty (should be rare), generate one directly
    return compress(uuid.uuid4().hex)


# Initialize the cache
if _guid_cache_enabled:
    fill_guid_cache()


# Modify create_guid to use the cache
def create_guid(name=None):
    """Creates a guid from a random name or bytes or generates a random guid"""
    return get_guid(name)
