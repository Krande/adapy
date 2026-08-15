// URL-param dispatch seam: give registered plugins a chance to claim/observe
// deep-link query params before/alongside core's own `?file=`/`?scope=` handling.
// A handler that returns true has consumed its params (core removes them from the
// pool). Additive — core keeps its existing param handling untouched.

import { makePluginContextStandalone } from "./context";
import { getUrlParamHandlers, disablePlugin } from "./registry";

/** Offer the current query params to plugin handlers. Returns the set of params
 * that a plugin consumed, so the caller can strip them. Every handler is
 * try/caught: a throwing handler disables its plugin, never the core load. */
export async function dispatchPluginUrlParams(
  search: string | URLSearchParams,
): Promise<Set<string>> {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const consumed = new Set<string>();
  const ctx = makePluginContextStandalone("");
  for (const { pluginId, handler } of getUrlParamHandlers(ctx)) {
    const values: Record<string, string> = {};
    let anyPresent = false;
    for (const key of handler.params) {
      const v = params.get(key);
      if (v !== null) {
        values[key] = v;
        anyPresent = true;
      }
    }
    if (!anyPresent) continue;
    try {
      const took = await handler.handle(makePluginContextStandalone(pluginId), values);
      if (took) for (const key of handler.params) consumed.add(key);
    } catch (err) {
      disablePlugin(pluginId, `url handler threw: ${String(err)}`);
    }
  }
  return consumed;
}
