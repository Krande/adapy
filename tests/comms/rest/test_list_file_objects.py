"""The storage panel's file listing never enumerates the hidden namespaces (the derived cache)."""

import asyncio

from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC
from ada.comms.rest.converter import HIDDEN_PREFIXES
from ada.comms.rest.handlers import _handle_list_file_objects
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import FileEntry


class _Storage:
    def __init__(self):
        self.skip = None

    async def list(self, scope, *, skip_prefixes=()):
        self.skip = tuple(skip_prefixes)
        return [FileEntry(key="m/a.step", size=1, last_modified="")]


def test_the_listing_skips_hidden_prefixes_in_storage():
    storage = _Storage()
    message = MessageDC(instance_id=1, command_type=CommandTypeDC.LIST_FILE_OBJECTS)
    reply = asyncio.run(_handle_list_file_objects(message, storage, Scope.shared()))
    assert reply
    # Asked of the storage layer, so derived blobs are never listed at all: the panel's load time
    # follows the files, not the conversion history.
    assert storage.skip == HIDDEN_PREFIXES


def test_every_source_the_server_accepts_is_listed():
    """A source uploaded and converted must show in Storage. The listing's own extension
    sets had drifted from the server's: a GeniE workspace (.gnx) was stored, converted and
    opened, then missing from the list."""
    from ada.comms.rest.converters.keys import ConverterRegistry, is_supported_source
    from ada.comms.rest.handlers import _infer_file_type

    for key in ("model.gnx", "deck.zip", "m/a.step", "T1.SIN"):
        assert is_supported_source(key), key
        assert _infer_file_type(key) is not None, key
    for ext in ConverterRegistry.all_sources():
        assert _infer_file_type(f"x{ext}") is not None, ext
    assert _infer_file_type("notes.txt") is None


def test_a_gnx_is_in_the_listing():
    class _Gnx(_Storage):
        async def list(self, scope, *, skip_prefixes=()):
            return [
                FileEntry(key="model.gnx", size=1, last_modified=""),
                FileEntry(key="notes.txt", size=1, last_modified=""),
            ]

    from ada.comms.fb_wrap_model_gen import MessageDC as _M

    reply = asyncio.run(
        _handle_list_file_objects(
            _M(instance_id=1, command_type=CommandTypeDC.LIST_FILE_OBJECTS), _Gnx(), Scope.shared()
        )
    )
    # The reply is the flatbuffer the panel decodes; the names travel in it as strings.
    assert b"model.gnx" in reply
    assert b"notes.txt" not in reply
