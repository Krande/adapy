// handleWebSocketMessage.ts
import { handleFlatbufferMessage } from "../fb_handling/handle_incoming_buffers";

export async function handleWebSocketMessage(event: MessageEvent): Promise<void> {
  try {
    if (event.data instanceof Blob) {
      const buffer = await event.data.arrayBuffer();
      await handleFlatbufferMessage(buffer);
    } else {
      console.log("Message from server", event.data);
    }
  } catch (err) {
    console.error("Error handling WebSocket message:", err);
  }
}
