// The desktop viewer's leg of viewer control: it already holds a websocket to its server, which
// relays a local client's VIEWER_COMMAND to it (`ada.comms.msg_handling.viewer_control`). The answer
// goes back through the same relay to the client that sent it (its instance id). A viewer that has
// not turned external controls on answers every command with a refusal, so the sender is not left
// waiting.

import type { Message } from "@/flatbuffers/wsock/message";

import { answerPayload, type ViewerAnswer } from "./answer";
import type { ViewerControlDeps } from "./commands";
import { answerBytes, readViewerCommand } from "./wire";

export const EXTERNAL_CONTROLS_OFF = "external controls are off in this viewer (Settings > External controls)";

export interface DesktopViewerControl {
  enabled(): boolean;
  deps(): Promise<ViewerControlDeps>;
  send(bytes: Uint8Array): Promise<void>;
  instanceId(): number;
}

/** Answer a VIEWER_COMMAND that came over the desktop websocket. */
export async function answerDesktopCommand(message: Message, io: DesktopViewerControl): Promise<void> {
  const command = readViewerCommand(message);
  if (!command) return;
  const answer: ViewerAnswer = io.enabled()
    ? await answerPayload(await io.deps(), command.payload)
    : { ok: false, error: EXTERNAL_CONTROLS_OFF, result: null, state: null };
  await io.send(answerBytes(command.requestId, answer, { kind: "local", instanceId: message.instanceId() }, io.instanceId()));
}
