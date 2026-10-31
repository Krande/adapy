// The provider store: the one scope provider data (any plugin spec with `asset_provider_id`, whatever
// system it reads) lives in, whichever scope asked for it. Its wire name is `providers`; only admins can
// open it directly. Every other scope reads the datasets an admin granted it (`provider-grants`).
// Everything a provider job writes -- staging, publishes, job summaries, the options an admin set
// per collection -- is in here, so that is the scope to read it back from.

/** The provider store's scope, as a URL part. */
export const PROVIDER_STORE_SCOPE = "providers";

/** What the scope picker calls it. */
export const PROVIDER_STORE_LABEL = "Provider store";
