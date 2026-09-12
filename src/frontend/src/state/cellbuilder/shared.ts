/**
 * Cross-slice PLUMBING every cellbuilder slice may reach for.
 *
 * Owns: the builder's cell-id sequence, the scope string backend calls are
 * keyed on, and the global progress-toast adapter procedural jobs report
 * through. No store state of its own — these are the shared primitives, kept in
 * one place so a slice never imports a sibling for them.
 */

import {LOCAL_MODEL_SCOPE, capabilities} from "@/services/capabilities";
import {useConversionStore, type ConversionJob} from "@/state/conversionStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

// Procedural compile is a worker task (NATS queue, polled via convertStatus),
// so it reports through the same global toast panel (ConversionProgress) that
// conversion/FEA use — a spinner+progress row that resolves to success (auto-
// hides) or a dismissible error card. Keyed by the model so a re-compile updates
// the same toast in place; the key doubles as the human-readable label.
export function proceduralToastKey(name: string): string {
  return `Procedural: ${name}`;
}

export function setProceduralToast(name: string, patch: Partial<ConversionJob>): void {
  const key = proceduralToastKey(name);
  const conv = useConversionStore.getState();
  const prev = conv.jobs[key];
  conv.setJob(key, {
    sourceKey: key,
    jobId: prev?.jobId ?? "",
    derivedKey: prev?.derivedKey ?? "",
    status: "queued",
    progress: 0,
    stage: "",
    error: null,
    startedAt: prev?.startedAt ?? Date.now(),
    ...patch,
  });
}

let _seq = 0;
export const nextId = () => `cb_${++_seq}`;

export function currentScopePart(): string {
  const scope = useScopeStore.getState().current;
  if (scope) return scopeUrlPart(scope);
  // No real scope selected -- true on the websocket path, which has no `/api/me` to populate
  // `useScopeStore` in the first place. `LOCAL_MODEL_SCOPE` is the explicit sentinel the ws/REST
  // parity plan calls for here, not the REST-oriented "user:me" default: that string would work
  // by accident today and quietly mean something real the day a local model syncs to a server
  // (docs/documents/ws_rest_parity.rst, "scope strings"). REST always resolves a real scope
  // before falling through to this branch, so this only ever fires on the websocket transport.
  return capabilities.transport === "ws" ? LOCAL_MODEL_SCOPE : "user:me";
}

