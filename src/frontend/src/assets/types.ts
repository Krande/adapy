/**
 * The browser's view of the asset-store contracts in `ada/assets/`.
 *
 * Two layers, kept apart on purpose:
 *
 *   Wire*   exactly what the REST routes send (snake_case, columnar). Only
 *           `./projection` and `./assetIndex` read these.
 *   the rest  what every other module in this directory reads: rows resolved
 *           through their column NAMES, revisions sorted, sets instead of arrays.
 *
 * Nothing here names a provider or a source format. A provider's vocabulary
 * reaches the browser only as opaque strings -- `kind`, `label`, `provider` --
 * which the tab displays and never branches on.
 */

export type DeliveryKind = "none" | "mesh" | "build";

// --- wire ----------------------------------------------------------------------

export interface WireHierarchySlice {
  readonly schema: string;
  readonly provider: string;
  readonly collection: string;
  readonly root: string | null;
  readonly produced_at: string;
  readonly depth: number;
  readonly cols: readonly string[];
  readonly rows: readonly (readonly unknown[])[];
}

/** The per-revision manifest fields the index route folds in with `manifests=true`. */
export interface WireManifestSummary {
  readonly provider: string;
  readonly node?: string | null;
  readonly delivery: DeliveryKind;
  readonly produced_at: string;
  readonly hierarchy_revision?: string | null;
}

export interface WireRevision {
  readonly revision: string;
  readonly files: readonly string[];
  readonly manifest?: WireManifestSummary;
  readonly manifest_error?: string;
}

export interface WireAssetIndex {
  readonly collections: Readonly<
    Record<string, readonly { readonly subject: string; readonly revisions: readonly WireRevision[] }[]>
  >;
  readonly malformed: readonly { readonly key: string; readonly reason: string }[];
}

export interface WireProvider {
  readonly id: string;
  readonly label: string;
  readonly live: boolean;
  readonly delivery: readonly string[];
}

// --- the browser's model -------------------------------------------------------

/** One row of a hierarchy, in core's vocabulary. */
export interface AssetNode {
  readonly id: string;
  readonly parent: string | null;
  readonly label: string;
  /** The provider's own node type. Opaque: shown, filtered on, never branched on. */
  readonly kind: string;
  readonly leaf: boolean;
  readonly delivery: DeliveryKind;
  readonly path?: string;
  /** Which provider produced this node. Always present after parsing: absent on
   *  the wire means "the slice's provider", and ./projection fills it in so a
   *  mixed collection and a single-source one read the same. */
  readonly provider: string;
}

export interface HierarchySlice {
  readonly schema: "ada.assets/hierarchy@1";
  readonly provider: string;
  readonly collection: string;
  /** null = a collection index (depth-bounded, claims no completeness);
   *  otherwise the subtree spine of this root (complete for that root). */
  readonly root: string | null;
  readonly producedAt: string;
  readonly depth: number;
  readonly nodes: readonly AssetNode[];
}

export interface ManifestSummary {
  readonly provider: string;
  readonly node: string | null;
  readonly delivery: DeliveryKind;
  readonly producedAt: string;
  readonly hierarchyRevision: string | null;
}

export interface AssetRevision {
  readonly revision: string;
  readonly files: ReadonlySet<string>;
  /** Present when the index was asked for manifests and this revision has one
   *  that core could read. */
  readonly manifest: ManifestSummary | null;
  /** Why a listed manifest could not be summarised, verbatim from the server. */
  readonly manifestError: string | null;
}

export interface AssetSubject {
  readonly collection: string;
  readonly subject: string;
  /** ASCENDING by revision, so the last element is the newest. */
  readonly revisions: readonly AssetRevision[];
}

export interface AssetIndex {
  readonly collections: ReadonlyMap<string, ReadonlyMap<string, AssetSubject>>;
  /** Keys under `assets/` that did not parse, with the server's reason. */
  readonly malformed: readonly string[];
}

export type ResolutionMode =
  | { readonly kind: "latest" }
  | { readonly kind: "as-of"; readonly revision: string }
  | { readonly kind: "run"; readonly revision: string };
