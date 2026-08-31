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

export interface ExternalModelProvider {
  id: string;
  label: string;
}
