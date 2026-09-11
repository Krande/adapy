Websocket request correlation
=============================

The viewer and the Python websocket server exchange one flatbuffer envelope,
``wsock.Message`` (``src/flatbuffers/schemas/message.fbs``). Until the field
below was added, a reply could only be matched to the command that caused it by
verb (``ServerReply.reply_to``): nothing could be awaited, and two commands of
the same verb in flight were indistinguishable.

The field
---------

``Message.request_id: string`` (optional; appended last, so older readers and
writers stay wire-compatible). A client that wants to await a reply assigns an
id and sets it on the command. An empty or absent value means "not correlated".

The echo rule
-------------

Every reply the server sends *in answer to a command* echoes that command's
``request_id``. This includes ``SERVER_REPLY``, ``MESH_INFO_REPLY``,
``LIST_WEB_CLIENTS`` and ``ERROR`` replies (an unknown command, or a handler
that raised after the command parsed), so an awaiting caller always settles
rather than timing out. Unsolicited pushes — a scene view pushed after a
finished procedure, a ping, a forwarded message — carry no ``request_id``
(forwarded bytes pass through unchanged, so an id set by the original sender
survives forwarding).

On the Python side all such replies are built through
``ada.comms.msg_handling.reply_to(message, **fields)``; both the websocket
handlers and the REST ``/rpc`` builders use it. A handler must never set
``request_id`` itself. Verbs that send no reply (``UPDATE_SCENE``,
``UPDATE_SERVER``, ``DELETE_FILE_OBJECT``, ``SHUTDOWN_SERVER``, ...) are
unchanged.

The client API
--------------

``comms.request(build, opts?)`` on the transport interface
(``src/frontend/src/utils/comms/types.ts``), implemented by both ``WSComms``
and ``RESTComms`` on top of ``utils/comms/wsRequests.ts``:

.. code-block:: typescript

    const reply: Message = await comms.request((requestId) => {
        const builder = new flatbuffers.Builder(1024);
        const idOffset = builder.createString(requestId);
        Message.startMessage(builder);
        Message.addCommandType(builder, CommandType.LIST_PROCEDURES);
        Message.addTargetGroup(builder, TargetType.SERVER);
        Message.addClientType(builder, TargetType.WEB);
        Message.addRequestId(builder, idOffset);
        builder.finish(Message.endMessage(builder));
        return builder.asUint8Array();
    }, { timeoutMs: 30_000 });

``build`` receives the id and must bake it into the serialized message. The
promise resolves with the reply that echoes the id, and rejects on timeout,
when the transport closes, when sending fails, or when the reply is a server
``ERROR`` (``ServerReplyError``, which carries the decoded reply).

Incoming buffers are routed to the pending-request map *first*. A correlated
reply resolves its promise and is not passed on; only uncorrelated messages
fall through to the verb-keyed ``switch(replyTo)`` dispatcher in
``utils/fb_handling/handle_incoming_buffers.ts``, which continues to serve
unsolicited pushes and the verbs that are still fire-and-forget. A verb
converted to the awaited form therefore updates any store itself from the
resolved reply — ``request_list_of_nodes`` is the reference conversion.

Building on it
--------------

The capabilities seam (``src/frontend/src/services/capabilities/``) can now
implement websocket verbs as ordinary async functions returning the typed
reply, instead of sending bytes and watching a store for a side effect.
