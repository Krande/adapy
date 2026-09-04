// The browser-side half of the external-model provider registry.
//
// WHY A SEPARATE MODULE. `externalModels.ts` reaches the worker, so importing it
// pulls in `viewerApi` and, through it, the OIDC client — which touches
// `sessionStorage` at module scope and cannot be loaded outside a browser. The
// registry itself depends on none of that, and keeping it here is what lets it
// be unit-tested directly, the same split `externalModelsBinding` and
// `externalModelUpload` already have.
//
// It is also the honest shape: this is the mirror of core's Python
// `providers.py`, which is likewise a registry module sitting apart from the
// catalogue implementation it holds.
//
// Consumers should import from `@/services/externalModels`, which re-exports
// everything here — there is one place to look, and the dispatch lives beside
// the names it dispatches on.

import type {
  ExternalCollection,
  ExternalModel,
  ExternalModelRevision,
} from "./externalModelTypes";

export class ExternalModelsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ExternalModelsError";
  }
}

/** Options every client-provider read accepts, mirroring the job path's. */
export interface ExternalModelClientOpts {
  /** Advisory. A client provider does its own caching, if any — this says the
   *  caller explicitly asked to re-read the source rather than reuse it. */
  refresh?: string;
  signal?: AbortSignal;
}

/** A provider whose catalogue is read IN THE BROWSER rather than on a worker.
 *
 *  The three required methods are the same three the worker-side protocol
 *  requires, in the same order, with the same meanings — that symmetry is the
 *  point, not a coincidence, and it is what lets the dispatch be a branch rather
 *  than an adapter.
 *
 *  Implement this when, and only when, the catalogue authorises as the END USER.
 *  A catalogue the deployment itself can read belongs on the worker: that keeps
 *  the credential out of the page, survives a browser with no session, and lets
 *  the job cache absorb repeat reads. Registering here trades all three away,
 *  and buys the one thing the worker cannot have — the user's own identity.
 *
 *  CONSEQUENCE WORTH KNOWING: these calls run wherever the page runs, so the
 *  catalogue's host must answer the browser's CORS preflight for the viewer's
 *  origin. That is a fact about the remote service, not something core can
 *  arrange, and it is the usual reason a working implementation still fails from
 *  a new deployment.
 */
export interface ExternalModelClient {
  listCollections(opts?: ExternalModelClientOpts): Promise<ExternalCollection[]>;
  listModels(
    collection: string,
    opts?: ExternalModelClientOpts,
  ): Promise<ExternalModel[]>;
  modelUrl(
    collection: string,
    modelId: string,
    opts?: ExternalModelClientOpts & {
      expiresInSeconds?: number;
      /** Fetch a specific stored version. Only ever set by a caller that got the
       *  id from `listModelRevisions`, so a provider without revisions never
       *  sees it. */
      revision?: string;
    },
  ): Promise<{ url: string; headers?: Record<string, string> }>;
  /** OPTIONAL, and presence IS the declaration — as with upload, and as on the
   *  worker side. A provider that keeps more than one version of a model
   *  implements this to enumerate them, and honours `revision` on `modelUrl` to
   *  serve one. The two travel together: listing revisions while ignoring
   *  `revision` gives a picker whose every entry silently serves the current
   *  version.
   *
   *  Returns an empty array for a model this provider does not version, so a
   *  caller needs no branch between "unversioned provider" and "unversioned
   *  model". */
  listModelRevisions?(
    collection: string,
    modelId: string,
    opts?: ExternalModelClientOpts,
  ): Promise<ExternalModelRevision[]>;
  /** OPTIONAL, and presence IS the declaration — exactly as on the worker side.
   *  A read-only catalogue implements nothing and the viewer never offers
   *  upload for it, rather than offering a control that fails when pressed. */
  modelUploadUrl?(
    collection: string,
    modelId: string,
    opts?: ExternalModelClientOpts & {
      expiresInSeconds?: number;
      contentType?: string;
    },
  ): Promise<{ url: string; method?: string; headers?: Record<string, string> }>;
}

// provider id -> (label, implementation). Insertion-ordered and idempotent by
// id, matching the worker-side registry's contract: re-registering replaces its
// own entry rather than duplicating it, so a plugin whose module is evaluated
// twice does not appear twice in the picker.
const clientProviders = new Map<
  string,
  { label: string; impl: ExternalModelClient }
>();

/** Register (or replace) a browser-side provider.
 *
 *  Call it from a plugin's `register()`, not at module scope: importing a module
 *  must not mutate a global registry, which is the same rule core's own
 *  worker-side package states and then follows. */
export function registerExternalModelClient(
  id: string,
  impl: ExternalModelClient,
  opts?: { label?: string },
): void {
  const key = (id || "").trim();
  if (!key) throw new ExternalModelsError("provider id must be a non-empty string");
  clientProviders.set(key, { label: opts?.label || key, impl });
}

export function unregisterExternalModelClient(id: string): void {
  clientProviders.delete((id || "").trim());
}

/** The implementation registered for `id`, or null if this provider is served by
 *  a worker. Exported so a caller that must branch — a diagnostic panel, a
 *  test — can, while the ordinary read paths never make the caller ask. */
export function externalModelClient(id: string): ExternalModelClient | null {
  return clientProviders.get((id || "").trim())?.impl ?? null;
}

/** `{id, label}` for every browser-side provider, in registration order. */
export function externalModelClientProviders(): { id: string; label: string }[] {
  return [...clientProviders].map(([id, { label }]) => ({ id, label }));
}

/** Test hook — drops every browser-side registration. */
export function resetExternalModelClients(): void {
  clientProviders.clear();
}
