import type { Comms } from "./types";
import { WSComms } from "./ws_comms";
import { RESTComms } from "./rest_comms";
import { runtime } from "@/runtime/config";

export type {
  Comms,
  CommsConnectHandler,
  CommsMessageHandler,
  Unsubscribe,
} from "./types";

// Singleton comms instance. Defaults to WS (desktop / dev). The hosted
// viewer mode injects window.COMMS_MODE = "rest" via the served HTML to
// switch to RESTComms — same artifact, runtime-selected transport.
export const comms: Comms = runtime.isRestMode() ? new RESTComms() : new WSComms();
