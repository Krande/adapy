// Keeps the scope-gated plugins' answer (`./pluginAccess`) in step with the open scope: asked when the
// first gated thing is registered, again on every scope switch, and when the tab comes back into view
// (an admin may have flipped a switch meanwhile). A change has the slot hosts look again.

import { scopeUrlPart, useScopeStore, type ScopeOption } from "@/state/scopeStore";
import { viewerApi } from "@/services/viewerApi";

import {
  anyGatedRegistration,
  refreshPluginAccess,
  setPluginAccessFetcher,
  subscribePluginAccess,
  whenGatedRegistered,
} from "./pluginAccess";
import { usePluginUiStore } from "./pluginUiStore";
import { notifyPluginActivationChanged } from "./registry";

let started = false;

const scopeKey = (s: ScopeOption | null): string | null => (s ? scopeUrlPart(s) : null);

/** Ask for the open scope, once there is one (it is set after `/api/me`, i.e. once signed in). */
function ask(): void {
  const key = scopeKey(useScopeStore.getState().current);
  if (key) void refreshPluginAccess(key);
}

export function startPluginAccessSync(): void {
  if (started) return;
  started = true;
  setPluginAccessFetcher((scope) => viewerApi.scopePluginAccess(scope));
  subscribePluginAccess(() => {
    // Panels with an activation predicate, and every host that reads the registry on render.
    notifyPluginActivationChanged();
    usePluginUiStore.setState((s) => ({ visible: { ...s.visible } }));
  });
  whenGatedRegistered(ask);
  useScopeStore.subscribe((s, prev) => {
    if (anyGatedRegistration() && scopeKey(s.current) !== scopeKey(prev.current)) ask();
  });
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && anyGatedRegistration()) ask();
    });
  }
}
