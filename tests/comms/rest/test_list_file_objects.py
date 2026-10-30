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
