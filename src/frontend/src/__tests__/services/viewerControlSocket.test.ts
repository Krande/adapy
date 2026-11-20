// Viewer control over the websocket envelope: the hosted viewer's socket (services/viewerControl/
// socket.ts) takes a ticket, opens `/api/viewer/ws`, answers each VIEWER_COMMAND with VIEWER_ANSWER
// and reports its state with VIEWER_STATE; the desktop viewer answers the same command relayed by
// its websocket server back to the local client that sent it -- or refuses it while external
// controls are off.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

import { CommandType } from "@/flatbuffers/commands/command-type";
import { TargetType } from "@/flatbuffers/commands/target-type";
import { Message } from "@/flatbuffers/wsock/message";
import { viewerSocketUrl } from "@/services/api/viewerControl";
import type { ViewerControlDeps } from "@/services/viewerControl/commands";
import { answerDesktopCommand, EXTERNAL_CONTROLS_OFF } from "@/services/viewerControl/desktop";
import { startViewerSocket, type SocketLike } from "@/services/viewerControl/socket";
import { parseBytes } from "@/services/viewerControl/wire";
import type { ViewportApi } from "@/viewport/api/viewportApi";

function commandBytes(requestId: string, payload: unknown, instanceId = 7): ArrayBuffer {
  const builder = new flatbuffers.Builder(256);
  const json = builder.createString(JSON.stringify(payload));
  const id = builder.createString(requestId);
  Message.startMessage(builder);
  Message.addInstanceId(builder, instanceId);
  Message.addCommandType(builder, CommandType.VIEWER_COMMAND);
  Message.addTargetGroup(builder, TargetType.WEB);
  Message.addRequestId(builder, id);
  Message.addPayloadJson(builder, json);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

function deps(): { deps: ViewerControlDeps; log: string[]; select(n: number): void } {
  const log: string[] = [];
  let selected = 0;
  const viewport = {
    kind: "native",
    selectionCount: () => selected,
    selection: () => [],
    selectionBounds: () => null,
    cameraPose: () => null,
    fitAll: () => log.push("fitAll"),
    setView: (v: string) => log.push(`setView:${v}`),
  } as unknown as ViewportApi;
  return {
    log,
    select: (n) => (selected = n),
    deps: {
      viewport: () => viewport,
      scope: () => "user:me",
      loadedSources: () => ["models/frame.glb"],
      activeSource: () => "models/frame.glb",
      clash: () => ({ runKey: null, joints: null, selectedJoint: null, hasJoint: () => false }),
      openRun: async () => {},
      focusJoint: async () => {},
      selectNames: async () => {},
      objectsNamed: async () => [],
      listDetailModels: async () => [],
      openDetailModel: async () => {},
      detailConnections: async () => [],
      settle: async () => {},
    },
  };
}

class FakeSocket implements SocketLike {
  binaryType = "blob";
  readyState = 0;
  sent: Message[] = [];
  closed: number | null = null;
  private listeners: Record<string, ((e: { data: unknown }) => void)[]> = {};
  constructor(readonly url: string) {}
  addEventListener(type: string, listener: (e: { data: unknown }) => void): void {
    (this.listeners[type] ??= []).push(listener);
  }
  send(data: Uint8Array): void {
    this.sent.push(parseBytes(data.slice()));
  }
  close(code?: number): void {
    this.closed = code ?? 1000;
  }
  emit(type: string, data?: unknown): void {
    if (type === "open") this.readyState = 1;
    if (type === "close") this.readyState = 3;
    for (const l of this.listeners[type] ?? []) l({ data });
  }
}

const tick = () => new Promise((r) => setTimeout(r, 10));

test("the hosted viewer's socket: a ticket, the socket, state on open, and each command answered", async () => {
  const d = deps();
  const sockets: FakeSocket[] = [];
  const tickets: unknown[] = [];
  let changed: () => void = () => {};
  const stop = startViewerSocket(
    d.deps,
    {
      ticket: async (client) => (tickets.push(client), { ticket: `t${tickets.length}`, path: "/api/viewer/ws" }),
      url: (path, ticket) => `ws://viewer.invalid${path}?ticket=${ticket}`,
      open: (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s;
      },
    },
    { client: { title: "viewer" }, watch: (cb) => ((changed = cb), () => {}), retryMs: 5, reportDelayMs: 1 },
  );
  await tick();
  assert.deepEqual(tickets, [{ title: "viewer" }]);
  const [ws] = sockets;
  assert.equal(ws.url, "ws://viewer.invalid/api/viewer/ws?ticket=t1");
  assert.equal(ws.binaryType, "arraybuffer");
  ws.emit("open");
  const [state] = ws.sent;
  assert.equal(state.commandType(), CommandType.VIEWER_STATE);
  assert.deepEqual(JSON.parse(state.payloadJson()!).loaded_models, ["models/frame.glb"]);

  ws.emit("message", commandBytes("c1", { commands: [{ action: "camera", fit: "all" }, { action: "camera", view: "top" }] }));
  await tick();
  const answer = ws.sent[1];
  assert.equal(answer.commandType(), CommandType.VIEWER_ANSWER);
  assert.equal(answer.requestId(), "c1");
  assert.equal(answer.targetGroup(), TargetType.SERVER);
  const body = JSON.parse(answer.payloadJson()!);
  assert.equal(body.ok, true);
  assert.deepEqual(body.steps.map((s: { status: string }) => s.status), ["done", "done"]);
  assert.deepEqual(d.log, ["fitAll", "setView:top"]);

  // A change is reported once; nothing changed, nothing sent.
  changed();
  await tick();
  assert.equal(ws.sent.length, 2, "the answer already carried this state");
  d.select(3);
  changed();
  await tick();
  assert.equal(ws.sent.length, 3);
  assert.equal(JSON.parse(ws.sent[2].payloadJson()!).selection.count, 3);

  // The socket drops: a new ticket, a new socket.
  ws.emit("close");
  await tick();
  assert.equal(sockets.length, 2);
  assert.equal(sockets[1].url, "ws://viewer.invalid/api/viewer/ws?ticket=t2");

  stop();
  assert.equal(sockets[1].closed, 1000);
});

test("the desktop viewer answers a relayed command to the client that sent it -- or refuses it while off", async () => {
  const d = deps();
  const sent: Message[] = [];
  const io = (enabled: boolean) => ({
    enabled: () => enabled,
    deps: async () => d.deps,
    send: async (bytes: Uint8Array) => void sent.push(parseBytes(bytes.slice())),
    instanceId: () => 42,
  });
  const message = parseBytes(commandBytes("c9", { command: { action: "camera", fit: "all" } }, 7));
  await answerDesktopCommand(message, io(true));
  await answerDesktopCommand(message, io(false));
  const [ok, refused] = sent;
  for (const m of [ok, refused]) {
    assert.equal(m.commandType(), CommandType.VIEWER_ANSWER);
    assert.equal(m.requestId(), "c9");
    assert.equal(m.targetGroup(), TargetType.LOCAL);
    assert.equal(m.targetId(), 7, "back to the local client that sent it");
    assert.equal(m.instanceId(), 42);
  }
  assert.equal(JSON.parse(ok.payloadJson()!).ok, true);
  assert.deepEqual(JSON.parse(refused.payloadJson()!), { ok: false, error: EXTERNAL_CONTROLS_OFF, result: null, state: null });
  assert.deepEqual(d.log, ["fitAll"], "the refused command never ran");
});

test("the socket's URL is on the API's origin, ws for http and wss for https", () => {
  assert.equal(viewerSocketUrl("https://viewer.invalid/app/", "/api", "/api/viewer/ws", "abc"), "wss://viewer.invalid/api/viewer/ws?ticket=abc");
  assert.equal(viewerSocketUrl("http://localhost:5173/", "http://localhost:8080/api", "/api/viewer/ws", "abc"), "ws://localhost:8080/api/viewer/ws?ticket=abc");
});
