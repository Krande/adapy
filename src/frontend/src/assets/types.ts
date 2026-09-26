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

/** Whoever made a change, on the wire -- adopted under core's own names
 *  (§Decision 7); nothing here is format-specific. */
export interface WireActor {
  readonly id: string;
  readonly display?: string | null;
  readonly application?: string | null;
}

/** Authorship recorded on ONE publish (§Decision 6). Every field optional, and
 *  an absent `WireChangeRecord` -- the field missing from `WireManifestSummary`
 *  entirely -- is a complete manifest, not a degraded one: a provider whose
 *  source carries no authorship leaves it out and the tab shows nothing, never
 *  a gap or an "unknown". `published_by`/`published_via` are core-stamped and
 *  trustworthy; `source_actor` is merely RELAYED by the provider from its own
 *  source and rendered "source says", never presented as the publisher --
 *  that split is the whole point of the field, see `./changes` and
 *  `AssetsTab`'s `Detail`. */
export interface WireChangeRecord {
  readonly published_by?: WireActor | null;
  readonly published_via?: "user" | "service" | null;
  readonly source_actor?: WireActor | null;
  readonly action?: "added" | "modified" | "deleted" | null;
  readonly source_instant?: string | null;
}

/** The per-revision manifest fields the index route folds in with `manifests=true`. */
export interface WireManifestSummary {
  readonly provider: string;
  readonly node?: string | null;
  readonly delivery: DeliveryKind;
  readonly produced_at: string;
  readonly hierarchy_revision?: string | null;
  readonly change?: WireChangeRecord | null;
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

export interface Actor {
  readonly id: string;
  readonly display: string | null;
  readonly application: string | null;
}

export interface ChangeRecord {
  readonly publishedBy: Actor | null;
  readonly publishedVia: "user" | "service" | null;
  readonly sourceActor: Actor | null;
  readonly action: "added" | "modified" | "deleted" | null;
  readonly sourceInstant: string | null;
}

export interface ManifestSummary {
  readonly provider: string;
  readonly node: string | null;
  readonly delivery: DeliveryKind;
  readonly producedAt: string;
  readonly hierarchyRevision: string | null;
  /** Present only when the manifest carries one -- §Decision 6, absent is
   *  normal and must render as nothing (no badge, no dimming, no "unknown"). */
  readonly change: ChangeRecord | null;
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

// --- attributes ------------------------------------------------------------------

// `GET /assets/attributes/{provider}/{collection}/{node}` answers ONE node. The
// document behind it covers a whole subject, and the route extracts from it, so
// what arrives here is proportional to the selection rather than to the subtree.
//
// A 404 is the ordinary answer for "nothing recorded" -- a provider that
// publishes no attributes, a document that does not mention the node, and a node
// that is not published all reach it -- so a caller renders absence, not an error.
export interface WireNodeAttributes {
  readonly node: string;
  readonly provider: string;
  readonly revision: string | null;
  readonly kind: string | null;
  /** The entity's own facts, by name. */
  readonly own: Readonly<Record<string, unknown>>;
  /** Named property sets, keeping the source's own grouping. */
  readonly groups: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  /** Quantities, kept apart so a consumer after numbers need not guess a group name. */
  readonly quantities: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
}

// --- delivery claims -------------------------------------------------------------
//
// `GET /assets/delivery/{provider}/{collection}/{node}` answers one of these two
// shapes (Decision 1's two delivery kinds). `./delivery` is the only module that
// reads a claim and turns it into scene content; every other module only ever
// sees `DeliveryKind` on a node/badge, never the claim itself.

export interface WireMeshDelivery {
  readonly kind: "mesh";
  /** For the built-in `published` provider this is a storage KEY, not an
   *  absolute URL -- `./delivery` resolves it through the blob route. A live
   *  provider's own claim (e.g. a presigned URL) is already absolute. */
  readonly url: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly source_up_axis: "z" | "y";
  readonly revision: string;
  readonly provider: string;
}

export interface WireBuildDelivery {
  readonly kind: "build";
  readonly capability: string;
  readonly options: Readonly<Record<string, unknown>>;
  readonly fingerprint_inputs: readonly string[];
  readonly revision: string;
  readonly provider: string;
}

export type WireDeliveryClaim = WireMeshDelivery | WireBuildDelivery;

export interface MeshDelivery {
  readonly kind: "mesh";
  readonly url: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly sourceUpAxis: "z" | "y";
  readonly revision: string;
  readonly provider: string;
}

export interface BuildDelivery {
  readonly kind: "build";
  readonly capability: string;
  readonly options: Readonly<Record<string, unknown>>;
  readonly fingerprintInputs: readonly string[];
  readonly revision: string;
  readonly provider: string;
}

export type DeliveryClaim = MeshDelivery | BuildDelivery;

/** `POST /assets/build`'s reply. `job_id: null` with `cached: true` means the
 *  summary already sits at `derived_key` -- nothing was enqueued. */
export interface WireBuildAssetResponse {
  readonly derived_key: string;
  readonly capability: string;
  readonly provider: string;
  readonly subject: string;
  readonly revision: string;
  readonly node: string;
  readonly fingerprint: string;
  readonly job_id: string | null;
  readonly cached: boolean;
}

// --- the change feed (`GET /scopes/{scope}/source-nodes`) ----------------------
//
// A different backend subsystem from the asset store above (`routes/source_nodes.py`,
// not `routes/assets.py`) -- kept in this file anyway so `./changes` and
// `services/api/sourceNodes` have exactly one place to import wire shapes from,
// the same discipline every other module in this directory already keeps.

export interface WireSourceNodeRow {
  readonly node_ref: string;
  readonly parent_ref: string | null;
  readonly name: string | null;
  readonly last_changed_at: string;
  readonly last_changed_by: string | null;
  readonly observed_at: string;
  /** Nullable, and absent entirely on a deployment that has not migrated the
   *  column in yet (additive, migration 030): only a node the sweep found
   *  added/modified/deleted carries one -- a pure roll-up ancestor row does
   *  not, and NOCHANGE is never a value this column holds (§Decision 7). */
  readonly action?: "added" | "modified" | "deleted" | null;
}

/** `GET .../source-nodes?source=&refs=a,b,c`'s reply. `unknown` names refs the
 *  feed has never recorded a row for, so a caller does not have to infer that
 *  from absence in `nodes` -- though `./changes` treats the two identically,
 *  since both mean "nobody looked". */
export interface WireSourceNodesRefsResponse {
  readonly scope: string;
  readonly source: string;
  readonly nodes: readonly WireSourceNodeRow[];
  readonly unknown: readonly string[];
}
