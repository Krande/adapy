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
//
// TWO KINDS OF PROVIDER, ONE SET OF CALLS. The job path above resolves a
// provider on the WORKER, which authenticates as the deployment's own service
// identity. That is right for a catalogue the deployment owns, and structurally
// wrong for one that authorises PER END USER: a worker identity is not the user,
// so a catalogue deriving access from the caller's own entitlements serves the
// worker whatever an unentitled principal gets — commonly an empty or partial
// listing, or an outright refusal. No grant fixes that; the worker is not the
// person, and for some catalogues it never can be.
//
// So a provider may instead register a BROWSER-SIDE implementation
// (`registerExternalModelClient`), which reads the catalogue from the page, as
// the signed-in user, using whatever token that user's session can mint. Every
// function below dispatches to one if it is registered for the named provider
// and falls through to the job otherwise, so a CONSUMER never learns which kind
// it is talking to — the same property the worker-side registry already has
// across catalogues, extended across identities. If a consumer ever has to know,
// this abstraction has failed.

import {
  bindingFor,
  EXTERNAL_MODELS_BINDING_KEY,
  parseBindingMap,
  type ExternalModelBindingMap,
} from "./externalModelsBinding";
import { viewerApi, type ScopeUrl } from "./viewerApi";
import {
  ExternalModelsError,
  externalModelClient,
  externalModelClientProviders,
} from "./externalModelClients";
import type {
  ExternalCollection,
  ExternalModel,
  ExternalModelProvider,
} from "./externalModelTypes";

export {
  bindingFor,
  EXTERNAL_MODELS_BINDING_KEY,
  parseBindingMap,
  type ExternalModelBindingMap,
};

// The vocabulary and the browser-side registry both live in leaf modules (see
// their headers), and are re-exported here so a consumer has one import to
// remember and never has to know which half of the API a name came from.
export type {
  ExternalCollection,
  ExternalModel,
  ExternalModelProvider,
} from "./externalModelTypes";
export {
  ExternalModelsError,
  externalModelClient,
  externalModelClientProviders,
  registerExternalModelClient,
  resetExternalModelClients,
  unregisterExternalModelClient,
  type ExternalModelClient,
  type ExternalModelClientOpts,
} from "./externalModelClients";

/** The plugin id core's built-in external-model backend registers under. */
export const EXTERNAL_MODELS_PLUGIN_ID = "external-models";

interface JobOptions {
  action: string;
  provider?: string;
  collection?: string;
  model_id?: string;
  content_type?: string;
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

/** Every provider this viewer can reach — those registered on the worker serving
 *  this capability, plus those registered in this page. A UI that offers a
 *  picker should call this rather than assuming a provider exists: which ones
 *  are present is a deployment choice, and now also a bundle choice.
 *
 *  A BROWSER-SIDE PROVIDER WINS a shared id. The pair arises on purpose — a
 *  catalogue may register both halves, the worker's serving whatever it can read
 *  as the deployment and the page's serving the user's own entitlements — and
 *  the browser-side one is the more capable of the two by construction, since it
 *  has an identity the worker cannot obtain. Preferring it silently is right;
 *  offering the same label twice and letting the user pick the crippled one is
 *  not.
 *
 *  The job failing does NOT fail the call when this page has providers of its
 *  own: a deployment can legitimately run no worker for this capability at all,
 *  and a browser-side catalogue must keep working when it does. With nothing
 *  registered here, the error propagates — it is then the only answer there is,
 *  and it names what the worker has, which is what a misconfigured deployment
 *  needs to see. */
export async function listProviders(
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<ExternalModelProvider[]> {
  const local: ExternalModelProvider[] = externalModelClientProviders();

  let remote: ExternalModelProvider[] = [];
  try {
    const out = await runAction<{ providers: ExternalModelProvider[] }>(
      { action: "list_providers", refresh: opts?.refresh },
      scope,
      opts?.signal,
    );
    remote = out.providers ?? [];
  } catch (e) {
    if (local.length === 0) throw e;
    // Not silent: a worker that was expected to answer and did not is worth
    // seeing, even though the page can carry on without it.
    console.warn(
      "[external-models] the worker did not answer list_providers; " +
        "listing only the providers registered in this page",
      e,
    );
  }

  const seen = new Set(local.map((p) => p.id));
  return [...local, ...remote.filter((p) => !seen.has(p.id))];
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
  const impl = externalModelClient(provider);
  if (impl) return (await impl.listCollections(opts)) ?? [];

  const out = await runAction<{ collections: ExternalCollection[] }>(
    { action: "list_collections", provider, refresh: opts?.refresh },
    scope,
    opts?.signal,
  );
  return out.collections ?? [];
}

/** The models, AND what the provider will let you do with them.
 *
 * Split from `listModels` rather than widening it: the models are what almost
 * every caller wants, and a call site that only lists should not have to
 * unwrap a capability it never asks about. */
export async function listModelsDetailed(
  provider: string,
  collection: string,
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<{ models: ExternalModel[]; canUpload: boolean }> {
  const impl = externalModelClient(provider);
  if (impl) {
    return {
      models: (await impl.listModels(collection, opts)) ?? [],
      // Presence is the declaration, same rule as the worker's `can_upload`.
      canUpload: typeof impl.modelUploadUrl === "function",
    };
  }

  const out = await runAction<{ models: ExternalModel[]; can_upload?: boolean }>(
    { action: "list_models", provider, collection, refresh: opts?.refresh },
    scope,
    opts?.signal,
  );
  return { models: out.models ?? [], canUpload: canUpload(out) };
}

export async function listModels(
  provider: string,
  collection: string,
  scope: ScopeUrl,
  opts?: { refresh?: string; signal?: AbortSignal },
): Promise<ExternalModel[]> {
  return (await listModelsDetailed(provider, collection, scope, opts)).models;
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
  const impl = externalModelClient(provider);
  if (impl) {
    const got = await impl.modelUrl(collection, modelId, {
      expiresInSeconds: opts?.expiresInSeconds,
      signal: opts?.signal,
    });
    if (!got?.url) throw new ExternalModelsError("provider returned no url");
    return { url: got.url, headers: got.headers ?? {} };
  }

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

// --- upload -----------------------------------------------------------------

// Re-exported so call sites keep one import, and imported because a re-export
// does not bind the name locally — both halves are used just below.
import { canUpload, prepareUploadBody } from "@/services/externalModelUpload";

export { canUpload, isGzipped, prepareUploadBody } from "@/services/externalModelUpload";

/** A short-lived URL to PUT one model to, with the headers the provider wants
 *  it stored under. Mint it at upload time; it expires. */
export async function modelUploadUrl(
  provider: string,
  collection: string,
  modelId: string,
  scope: ScopeUrl,
  opts?: { expiresInSeconds?: number; contentType?: string; signal?: AbortSignal },
): Promise<{ url: string; method: string; headers: Record<string, string> }> {
  const impl = externalModelClient(provider);
  if (impl) {
    if (typeof impl.modelUploadUrl !== "function") {
      // Same refusal the worker gives, and for the same reason: this is not a
      // malformed request, it is a catalogue that does not accept models.
      throw new ExternalModelsError(
        `provider '${provider}' does not accept uploads; it publishes through its own ` +
          "pipeline, so there is nothing for the viewer to upload to",
      );
    }
    const got = await impl.modelUploadUrl(collection, modelId, {
      expiresInSeconds: opts?.expiresInSeconds,
      contentType: opts?.contentType,
      signal: opts?.signal,
    });
    if (!got?.url) {
      throw new ExternalModelsError("provider returned no upload url");
    }
    return {
      url: got.url,
      method: got.method || "PUT",
      headers: got.headers ?? {},
    };
  }

  const out = await runAction<{
    url: string;
    method?: string;
    headers?: Record<string, string>;
  }>(
    {
      action: "model_upload_url",
      provider,
      collection,
      model_id: modelId,
      content_type: opts?.contentType,
      expires_in_seconds: opts?.expiresInSeconds,
      // Signed and short-lived, so never cacheable — the same reason model_url
      // busts unconditionally.
      refresh: catalogueNonce(),
    },
    scope,
    opts?.signal,
  );
  if (!out.url) throw new ExternalModelsError("provider returned no upload url");
  return { url: out.url, method: out.method || "PUT", headers: out.headers ?? {} };
}

/** Upload one model. Returns nothing; throws with the store's status on failure. */
export async function uploadModel(
  provider: string,
  collection: string,
  modelId: string,
  file: Blob,
  scope: ScopeUrl,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  const target = await modelUploadUrl(provider, collection, modelId, scope, {
    signal: opts?.signal,
  });
  const { body, headers } = await prepareUploadBody(file, target.headers);
  const res = await fetch(target.url, {
    method: target.method,
    body,
    headers,
    signal: opts?.signal,
  });
  if (!res.ok) {
    throw new ExternalModelsError(
      `uploading ${modelId} failed: HTTP ${res.status} ${res.statusText}`.trim(),
    );
  }
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
