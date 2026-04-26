import type { Comms } from "./types";
import { WSComms } from "./ws_comms";

export type { Comms, CommsMessageHandler, Unsubscribe } from "./types";

// Singleton comms instance. WS by default; the REST impl will be selected
// at startup via runtime config in a future change.
export const comms: Comms = new WSComms();
