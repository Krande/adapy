// Run point for the `renderableFileProviders` slot (plugin API 1.5.0): the place
// core hands a stored file to the plugin that claimed it.
//
// The shape mirrors `./sidecarLoaders` — a registry the UI never touches
// directly, a context built per plugin id, and failure isolated to the plugin
// that caused it.
//
// NO BOOKKEEPING LIVES HERE, and that is a design decision rather than an
// omission. A provider registers its model under the FILE KEY (see
// `RenderableFileProvider.open`), so core's existing scene state answers "is this
// file shown?" for a plugin-opened file exactly as it does for a converted one —
// the row's checkbox, the eye marker, the gallery anchor and the unload path all
// keep working with no new map to keep in step with them and no new place for
// core and a plugin to disagree about what is in the scene.
//
// DELIBERATELY LIGHT AT MODULE SCOPE. `./context` reaches into the mounted
// viewer's runtime, the stores and the loaders (three, GLTFLoader, the meshopt
// decoder), so it arrives through a dynamic import inside `openRenderableFile` —
// the same way `context.ts` pulls the model loader in. That keeps this module
// cheap enough to import from the load queue, which is on the boot path.

import {
  disablePlugin,
  getRenderableFileProviders,
  type RenderableFileProvider,
} from "./registry";

/** One entry of an already-enumerated provider list. */
export interface ProviderEntry {
  pluginId: string;
  provider: RenderableFileProvider;
}

/**
 * The first provider in the given (already ordered) list that claims the key.
 *
 * Split out from `openRenderableFile` so the resolution RULE can be tested
 * without a mounted viewer: everything around it — building a plugin context,
 * loading a GLB — needs a browser, and the rule is the part that decides which
 * plugin gets the file.
 *
 * A throwing `claims` disables its plugin and the walk continues to the next
 * provider: the question "who can open this" has other candidates, and one
 * broken plugin must not make a file unopenable by a working one.
 */
export function pickRenderableProvider(
  entries: readonly ProviderEntry[],
  key: string,
): ProviderEntry | null {
  for (const entry of entries) {
    try {
      if (entry.provider.claims(key)) return entry;
    } catch (err) {
      disablePlugin(
        entry.pluginId,
        `renderable provider "${entry.provider.id}" claims() threw: ${String(err)}`,
      );
    }
  }
  return null;
}

/**
 * Offer one stored file to the registered providers; returns true when one took
 * it and the model is in the scene.
 *
 * ORDER OF RESOLUTION IS THE CALLER'S, NOT THIS FUNCTION'S. Core's own paths
 * (the streaming bake, the legacy convert pipeline) are tried first, in
 * `loadQueueStore`, and this is reached only for a file neither of them handles.
 * Providers fill gaps; they do not override. A plugin that could claim `.ifc`
 * therefore cannot change how an `.ifc` loads, which is the property that makes
 * installing a plugin a safe thing to do.
 *
 * Among providers, the first to `claims` the key in `(order, id)` order wins and
 * the rest are not consulted. Deterministic on purpose: two plugins claiming one
 * key is a deployment mistake, and it should at least misbehave the same way on
 * every machine.
 */
export async function openRenderableFile(key: string, scope: string): Promise<boolean> {
  const { makePluginContextStandalone } = await import("./context");
  // A throwaway ctx just to enumerate active providers; the winner gets its own
  // plugin-scoped ctx below.
  const enumCtx = makePluginContextStandalone("");
  const picked = pickRenderableProvider(getRenderableFileProviders(enumCtx), key);
  if (!picked) return false;

  const ctx = makePluginContextStandalone(picked.pluginId);
  // NOT wrapped in a try/catch that swallows. A provider that claimed the file
  // and then failed has produced a load ERROR for that file, and the load
  // queue's own handler is what puts it in front of the user; turning it into
  // `false` here would fall back to a convert pipeline that cannot read the file
  // either, and report that second, less true failure instead.
  await picked.provider.open(ctx, { key, scope });
  return true;
}
