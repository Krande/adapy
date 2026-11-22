// Robot-asset bundles (`.adabots`): the wire shapes of `/api/admin/robot-assets` and the rules the
// Admin panel's "Robot bundles" tab draws by. React-free, so the rules are testable on their own.
// The format and the import rules are in docs/architecture/robot_asset_bundles.md; the server side
// is ada/comms/rest/robot_bundles.py.

/** The bundle file's suffix. A zip by content. */
export const ROBOT_BUNDLE_SUFFIX = ".adabots";
export const ROBOT_BUNDLE_SCHEMA = "ada.robots/bundle@1";

export interface RobotAssetVersion {
  ref: string;
  version: string;
  version_scheme: "revision" | "semver" | "natural";
  latest: boolean;
  files: number;
  bytes: number;
  manifest_sha256: string | null;
  last_modified: string | null;
  metadata: Record<string, unknown>;
  problem: string | null;
}

export interface RobotAssetRow {
  id: string;
  name: string;
  latest: string;
  /** Newest first. */
  versions: RobotAssetVersion[];
}

export interface RobotAssetGrant {
  target_scope: string;
  collection: string | null;
  geometry: boolean;
}

export interface RobotBundleLimits {
  max_bundle_bytes: number;
  max_total_bytes: number;
  max_file_bytes: number;
  max_files: number;
  max_robots: number;
}

export interface RobotAssetList {
  store: string;
  provider: string;
  collection: string;
  robots: RobotAssetRow[];
  grants: RobotAssetGrant[];
  bundle: { schema: string; suffix: string; limits: RobotBundleLimits };
}

/** What an import would do with one robot of a bundle. */
export type RobotImportStatus = "new" | "unchanged" | "newer" | "older" | "conflict" | "invalid";

export interface RobotImportPlanRow {
  ref: string;
  id: string;
  version: string;
  version_scheme: string;
  name: string;
  status: RobotImportStatus;
  detail: string;
  latest_before: string | null;
  stored_versions: string[];
  files: number;
  bytes: number;
  content_sha256: string;
  metadata: Record<string, unknown>;
  problems: string[];
  duplicate_of: string | null;
}

export interface RobotImportPreview {
  upload_id: string;
  bundle: { schema: string; exported_at: string | null; adapy_version: string | null; robots: number };
  robots: RobotImportPlanRow[];
  counts: Record<RobotImportStatus, number>;
  grants: RobotAssetGrant[];
}

/** What the admin chose in the preview, as the import route takes it. */
export interface RobotImportChoices {
  add_older?: boolean;
  robots?: Record<string, { include?: boolean; add_older?: boolean }>;
}

export type RobotImportResultKind = "added" | "added-older" | "skipped" | "conflict" | "failed";

export interface RobotImportResultRow {
  ref: string;
  id: string;
  version: string;
  name: string;
  planned: RobotImportStatus;
  result: RobotImportResultKind;
  detail: string;
}

export interface RobotImportResult {
  results: RobotImportResultRow[];
  counts: Partial<Record<RobotImportResultKind, number>>;
  index_written: boolean;
}

/** The refused bundle's body: `{detail: {message, problems}}`. */
export function refusalProblems(detail: string | undefined): string[] {
  if (!detail) return [];
  try {
    const d = (JSON.parse(detail) as { detail?: unknown }).detail;
    if (d && typeof d === "object" && Array.isArray((d as { problems?: unknown }).problems)) {
      return ((d as { problems: unknown[] }).problems).map(String);
    }
    if (typeof d === "string") return [d];
  } catch {
    // not JSON
  }
  return [];
}

// --- the list's selection ---------------------------------------------------------------------------

/** Every ref the list shows. */
export function allRefs(rows: readonly RobotAssetRow[]): string[] {
  return rows.flatMap((r) => r.versions.map((v) => v.ref));
}

/** Toggle one ref. */
export function toggleRef(selected: ReadonlySet<string>, ref: string): Set<string> {
  const next = new Set(selected);
  if (next.has(ref)) next.delete(ref);
  else next.add(ref);
  return next;
}

/** A robot's checkbox: every version of it, on or off together. */
export function robotSelection(selected: ReadonlySet<string>, row: RobotAssetRow): "all" | "some" | "none" {
  const n = row.versions.filter((v) => selected.has(v.ref)).length;
  return n === 0 ? "none" : n === row.versions.length ? "all" : "some";
}

export function toggleRobot(selected: ReadonlySet<string>, row: RobotAssetRow): Set<string> {
  const next = new Set(selected);
  const on = robotSelection(selected, row) !== "all";
  for (const v of row.versions) {
    if (on) next.add(v.ref);
    else next.delete(v.ref);
  }
  return next;
}

/** Drop refs the list no longer has (after a refresh or an import). */
export function pruneSelection(selected: ReadonlySet<string>, rows: readonly RobotAssetRow[]): Set<string> {
  const known = new Set(allRefs(rows));
  return new Set([...selected].filter((r) => known.has(r)));
}

/** The export body for a selection: the refs in a stable order. */
export function exportRefs(selected: ReadonlySet<string>): string[] {
  return [...selected].sort();
}

// --- the preview ------------------------------------------------------------------------------------

/** The words a preview row's status is shown with. */
export function statusLabel(row: Pick<RobotImportPlanRow, "status" | "version">): string {
  switch (row.status) {
    case "new":
      return "New";
    case "unchanged":
      return "Unchanged";
    case "newer":
      return `Newer: adds version ${row.version}`;
    case "older":
      return "Older: skipped";
    case "conflict":
      return "Conflict";
    case "invalid":
      return "Invalid";
  }
}

/** The tone a status is drawn in. */
export function statusTone(status: RobotImportStatus | RobotImportResultKind): "good" | "info" | "warn" | "bad" | "muted" {
  switch (status) {
    case "new":
    case "newer":
    case "added":
    case "added-older":
      return "good";
    case "unchanged":
    case "skipped":
      return "muted";
    case "older":
      return "info";
    case "conflict":
      return "warn";
    case "invalid":
    case "failed":
      return "bad";
  }
}

/** A preview row's label once the choices are in: an older version ticked to be added says so, a
 *  new one left out says that. */
export function planLabel(row: RobotImportPlanRow, choices: RobotImportChoices): string {
  if (row.status === "older" && willWrite(row, choices)) return "Older: adds a past version";
  if ((row.status === "new" || row.status === "newer") && !willWrite(row, choices)) return `${statusLabel(row)} (left out)`;
  return statusLabel(row);
}

/** Whether a row is written by an import with these choices. */
export function willWrite(row: RobotImportPlanRow, choices: RobotImportChoices): boolean {
  const mine = choices.robots?.[row.ref] ?? {};
  if (mine.include === false) return false;
  if (row.status === "new" || row.status === "newer") return true;
  if (row.status === "older") return mine.add_older ?? choices.add_older ?? false;
  return false;
}

/** How many robots an import with these choices writes: what the Import button says. */
export function writeCount(rows: readonly RobotImportPlanRow[], choices: RobotImportChoices): number {
  return rows.filter((r) => willWrite(r, choices)).length;
}

/** The choices with one robot's field set. */
export function setRobotChoice(
  choices: RobotImportChoices,
  ref: string,
  field: "include" | "add_older",
  value: boolean,
): RobotImportChoices {
  return { ...choices, robots: { ...(choices.robots ?? {}), [ref]: { ...(choices.robots?.[ref] ?? {}), [field]: value } } };
}

/** The bundle-wide "add older versions" switch: it replaces every robot's own add_older choice
 *  (their include choices stay). */
export function setAddOlderForAll(choices: RobotImportChoices, value: boolean): RobotImportChoices {
  const robots: NonNullable<RobotImportChoices["robots"]> = {};
  for (const [ref, c] of Object.entries(choices.robots ?? {})) {
    const { add_older: _dropped, ...rest } = c;
    if (Object.keys(rest).length) robots[ref] = rest;
  }
  return { ...choices, add_older: value, robots };
}

/** The import results, said in one line. */
export function resultSummary(result: RobotImportResult): string {
  const c = result.counts;
  const parts: string[] = [];
  const added = (c.added ?? 0) + (c["added-older"] ?? 0);
  if (added) parts.push(`${added} added`);
  if (c.skipped) parts.push(`${c.skipped} skipped`);
  if (c.conflict) parts.push(`${c.conflict} in conflict`);
  if (c.failed) parts.push(`${c.failed} failed`);
  return parts.join(" · ") || "nothing to import";
}

/** A file picked or dropped, checked before it is sent: a bundle by its suffix (or a plain .zip).
 *  The server validates the content either way. */
export function bundleFileProblem(file: { name: string; size: number }, limits?: RobotBundleLimits): string | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(ROBOT_BUNDLE_SUFFIX) && !name.endsWith(".zip")) {
    return `${file.name} is not a robot bundle (${ROBOT_BUNDLE_SUFFIX})`;
  }
  if (file.size === 0) return `${file.name} is empty`;
  if (limits && file.size > limits.max_bundle_bytes) {
    return `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(limits.max_bundle_bytes)}`;
  }
  return null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}

/** The download's file name from the response, else one made up. */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  const m = header ? /filename="([^"]+)"/.exec(header) : null;
  return m ? m[1] : fallback;
}
