// The external-model vocabulary, in a leaf module so both halves of the API can
// name it without importing each other.
//
// These are the wire shapes core's Python `Collection` / `ExternalModel`
// dataclasses serialise to, and a browser-side provider constructs them
// directly. One definition either way: a provider that returns the same fields
// is the same kind of thing to a consumer, whichever side of the wire it ran on.
//
// Consumers should import these from `@/services/externalModels`, which
// re-exports them.

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

/** One stored version of a model, for providers that keep more than one.
 *
 *  `id` is opaque and provider-defined; consumers pass it back verbatim and must
 *  not parse it. `name` is what a person picks from. A provider that cannot date
 *  its revisions should return them newest-first, because that is the order a UI
 *  falls back to when `createdAt` is absent. */
export interface ExternalModelRevision {
  id: string;
  name: string;
  createdAt?: string | null;
  size?: number | null;
  /** The one a fetch without a revision resolves to. Exactly one per list. */
  current?: boolean;
}

export interface ExternalModelProvider {
  id: string;
  label: string;
}
