// External models — the viewer-side half of core's external-model API.
//
// WHY THIS IS IN CORE AND NOT IN A PLUGIN. adapy's Vite resolver maps
// `@adapy-plugins/<name>` to `packages/plugins/<name>/src/register.tsx` and
// refuses any specifier with a slash, so one plugin importing another's client
// is a BUILD-TIME link: both plugins must be compiled into the same bundle or
// the importing one fails to build. A panel that wants "load a model from
// somewhere else" therefore could not be shipped without also shipping whichever
// vendor plugin happened to provide that catalogue — and a deployment without
// access to that vendor could not have the panel at all.
//
// Routing it through core inverts that. A consumer imports from here, names a
// provider id, and gets the same three calls whether the models come from a
// vendor catalogue or from an object-store bucket. Neither plugin needs the
// other in its bundle, and there is no hand-transcribed .d.ts to keep in sync
// with another repo's re-export block.
//
// The backend surface is a job, not a route: adapy mounts two hardcoded routers
// and gives plugins no way to contribute FastAPI routes, so every read is
// enqueue -> poll -> read the derived blob. Core hashes `options` into the job's
// synthetic source key, so an identical repeat request cache-hits a finished job
// and costs one round-trip rather than a rebuild.

import {
  bindingFor,
  EXTERNAL_MODELS_BINDING_KEY,
  parseBindingMap,
  type ExternalModelBindingMap,
} from "./externalModelsBinding";
import { viewerApi, type ScopeUrl } from "./viewerApi";

export {
  bindingFor,
  EXTERNAL_MODELS_BINDING_KEY,
  parseBindingMap,
  type ExternalModelBindingMap,
};

/** A top-level grouping in a provider — a bucket prefix, a vendor project. */
export interface ExternalCollection {
  id: string;
  name: string;
}

/** One loadable model. `key` is provider-internal; consumers should treat it as
 *  opaque and address a model by `(collection, id)`. */
export interface ExternalModel {
  id: string;
  name: string;
  collection: string;
  key: string;
  size?: number | null;
}

export interface ExternalModelProvider {
  id: string;
  label: string;
}

/** The plugin id core's built-in external-model backend registers under. */
export const EXTERNAL_MODELS_PLUGIN_ID = "external-models";

export class ExternalModelsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ExternalModelsError";
  }
}

interface JobOptions {
  action: string;
  provider?: string;
  collection?: string;
  model_id?: string;
  expires_in_seconds?: number;
  /** Opaque token folded into the options hash to deliberately MISS the job
   *  cache. Pass one when the user explicitly asked to re-read the source. */
  refresh?: string;
}

/** Derived summaries are stored GZIPPED above a size threshold, so a small
 *  payload arrives as plain JSON and a large one does not. Decoding blindly as
 *  text works until a catalogue grows — the first big collection fails with
 *  `Unexpected token '\x1f'`, which reads like a corrupt response rather than a
 *  compressed one. Sniff the magic number instead of guessing from size. */
async function decodeSummary(buf: ArrayBuffer): Promise<string> {
  const bytes = new Uint8Array(buf);
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    const stream = new Blob([buf]).stream().pipeThrough(
      new DecompressionStream("gzip"),
    );
    return await new Response(stream).text();
  }
  return new TextDecoder().decode(buf);
}

const POLL_INTERVAL_MS = 800;
const POLL_TIMEOUT_MS = 60_000;

/** Enqueue one catalogue action, poll to completion, return the parsed summary.
 *
 *  Errors are surfaced as ExternalModelsError with the backend's own message —
 *  in particular "unknown external-model provider 'x' (registered: …)", which
 *  names what IS registered and is usually the fastest way to see that a
 *  provider's module simply was not preloaded on the worker. */
async function runAction<T>(
  options: JobOptions,
  scope: ScopeUrl,
  signal?: AbortSignal,
): Promise<T> {
  // The enqueue returns only {job_id, derived_key} — no status — so the first
  // real status has to come from a poll. A cache-hit job is already `done` on
  // that first read and costs no extra wait.
  const { job_id, derived_key } = await viewerApi.pluginJob(
    EXTERNAL_MODELS_PLUGIN_ID,
    { options: options as unknown as Record<string, unknown> },
    { scope },
  );

  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let status = await viewerApi.convertStatus(job_id);
  while (status.status !== "done" && status.status !== "error") {
    if (signal?.aborted) throw new ExternalModelsError("aborted");
    if (Date.now() > deadline) {
      throw new ExternalModelsError(
        `external-models ${options.action} timed out after ${POLL_TIMEOUT_MS / 1000}s`,
      );
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    status = await viewerApi.convertStatus(job_id);
  }
  if (status.status === "error") {
    throw new ExternalModelsError(
      status.error || `external-models ${options.action} failed`,
    );
  }

  // Read the summary from the key the ENQUEUE named. The status row echoes a
  // derived_key too, but the enqueue's is the one core wrote the options hash
  // into, so it is the authoritative one for a cache-hit.
  const buf = await viewerApi.getBlob(scope, derived_key);
  const text = await decodeSummary(buf);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ExternalModelsError(
      `external-models ${options.action} returned a non-JSON summary`,
    );
  }
}

/** Providers registered on the worker serving this capability. A UI that offers
 *  a picker should call this rather than assuming a provider exists — which one
 *  is present is a deployment choice. */
export async function listProviders(
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<ExternalModelProvider[]> {
  const out = await runAction<{ providers: ExternalModelProvider[] }>(
    { action: "list_providers", refresh: opts?.refresh },
    scope,
    opts?.signal,
  );
  return out.providers ?? [];
}

/** A cache-busting token for one read of the catalogue.
 *
 *  Core hashes a job's `options` into its source key, so an identical request
 *  cache-hits a finished job FOREVER — which means a UI that never varies its
 *  options can never observe a configuration change. A deployment switched from
 *  the stub to a real bucket kept serving the stub's fixtures indefinitely.
 *
 *  One token per mount (or per explicit refresh), reused across that view's
 *  calls: fresh data when a view opens, and the cache still absorbs re-renders. */
export function catalogueNonce(): string {
  return Date.now().toString(36);
}

export async function listCollections(
  provider: string,
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<ExternalCollection[]> {
  const out = await runAction<{ collections: ExternalCollection[] }>(
    { action: "list_collections", provider, refresh: opts?.refresh },
    scope,
    opts?.signal,
  );
  return out.collections ?? [];
}

export async function listModels(
  provider: string,
  collection: string,
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<ExternalModel[]> {
  const out = await runAction<{ models: ExternalModel[] }>(
    { action: "list_models", provider, collection, refresh: opts?.refresh },
    scope,
    opts?.signal,
  );
  return out.models ?? [];
}

/** A short-lived, presigned URL for one model. Not cached beyond its expiry —
 *  mint it at load time rather than holding it in component state. */
export async function modelUrl(
  provider: string,
  collection: string,
  modelId: string,
  scope: ScopeUrl,
  opts?: { expiresInSeconds?: number; signal?: AbortSignal },
): Promise<{ url: string; headers: Record<string, string> }> {
  const out = await runAction<{ url: string; headers?: Record<string, string> }>(
    {
      action: "model_url",
      provider,
      collection,
      model_id: modelId,
      expires_in_seconds: opts?.expiresInSeconds,
      // ALWAYS bust the cache. The result is a short-lived signed URL, so a
      // cache hit returns one minted for an earlier request — which the store
      // then rejects as expired. Unlike the listing actions this is never
      // cacheable, so the token is unconditional rather than a caller's choice.
      refresh: catalogueNonce(),
    },
    scope,
    opts?.signal,
  );
  if (!out.url) throw new ExternalModelsError("provider returned no url");
  // Headers are empty for a provider whose URL carries its own signature, and
  // populated for one whose fetch must be authenticated. Returning them beside
  // the URL is what lets a single call site serve both without knowing which.
  return { url: out.url, headers: out.headers ?? {} };
}

// --- scope binding ----------------------------------------------------------

/** Read the scope -> provider:collection binding. Missing or malformed reads as
 *  an empty map rather than throwing: an unbound deployment is the normal case,
 *  and a panel must not fail to render because nobody has bound anything yet. */
export async function loadBindingMap(): Promise<ExternalModelBindingMap> {
  try {
    return parseBindingMap(
      await viewerApi.getPublicSetting(EXTERNAL_MODELS_BINDING_KEY),
    );
  } catch {
    return {};
  }
}

/** Admin-only. Writes go through `adminSetSetting`; the `public.` prefix governs
 *  READ access only and has no public setter. */
export async function saveBindingMap(
  map: ExternalModelBindingMap,
): Promise<void> {
  await viewerApi.adminSetSetting(
    EXTERNAL_MODELS_BINDING_KEY,
    JSON.stringify(map),
  );
}
