import type { Comms } from "./types";
import { WSComms } from "./ws_comms";
import { RESTComms } from "./rest_comms";

export type {
  Comms,
  CommsConnectHandler,
  CommsMessageHandler,
  Unsubscribe,
} from "./types";

// Singleton comms instance. Defaults to WS (desktop / dev). The hosted
// viewer mode injects window.COMMS_MODE = "rest" via the served HTML to
// switch to RESTComms — same artifact, runtime-selected transport.
function pickComms(): Comms {
  const mode = (window as any).COMMS_MODE;
  return mode === "rest" ? new RESTComms() : new WSComms();
}

export const comms: Comms = pickComms();
