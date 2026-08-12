// Pure helpers + types for the viewer "Stats" panel (model quantity take-off).
//
// The backend (ada.topo_model.takeoff.model_takeoff) computes a discipline-
// organised take-off at procedural compile and stores it as a `.stats.json`
// sidecar of the GLB; the frontend fetches it and renders the compact card +
// detailed panel. Everything here is pure (formatting + derivation) so it is
// unit-tested in isolation — no store/DOM access.

export type DisciplineKey = "structural" | "piping" | "hvac" | "electrical";

export interface DisciplineSummary {
  key: DisciplineKey;
  name: string;
  mass: number; // tonne
  cog: [number, number, number]; // m
  count: number;
}

export interface BeamRow {
  section: string;
  count: number;
  length: number;
  mass: number;
}
export interface PlateRow {
  label: string;
  thickness: number;
  count: number;
  area: number;
  mass: number;
}
export interface PipeRow {
  size: string;
  segments: number;
  length: number;
  mass: number;
}
export interface DuctRow {
  size: string;
  segments: number;
  length: number;
  area: number;
  mass: number;
}
export interface TrayRow {
  size: string;
  segments: number;
  length: number;
  mass: number;
}
export interface CableRow {
  type: string;
  length: number;
}
export interface FittingRow {
  name: string;
  count: number;
}
export interface MajorItem {
  name: string;
  discipline: string;
  mass: number;
  cog: [number, number, number];
}

/** A fabrication-detail joint type roll-up (the Detailing tab's "N detected"). */
export interface JointTypeRow {
  slug: string;
  name: string;
  count: number;
}
/** One detailing joint instance (from the compiled model's Connection parts). */
export interface JointItem {
  name: string;
  slug: string;
  type: string;
  members: string[];
  plates: number;
  welds: number;
  centre: [number, number, number] | null;
}
/** Per-joint overview computed at compile (present only for detail models). */
export interface JointsTakeoff {
  count: number;
  by_type: JointTypeRow[];
  items: JointItem[];
}

export interface ModelStats {
  schema_version: number;
  source_name: string | null;
  units: { length: string; mass: string; area: string };
  objects: number;
  total_mass: number;
  total_cog: [number, number, number];
  bbox: [number, number, number];
  disciplines: DisciplineSummary[];
  structural: { mass: number; beams: BeamRow[]; plates: PlateRow[] };
  piping: { mass: number; segments: PipeRow[]; fittings: FittingRow[] };
  hvac: { mass: number; segments: DuctRow[]; fittings: FittingRow[] };
  electrical: { mass: number; trays: TrayRow[]; cables: CableRow[] };
  /** Fabrication-detail joints overview — present (schema_version ≥ 2) only when
   * the model was compiled with a detailing engine; absent/empty otherwise. */
  joints?: JointsTakeoff;
  major_items: MajorItem[];
}

// The four discipline hues (matches the approved design; dark-panel values).
export const DISCIPLINE_META: Record<DisciplineKey, { name: string; color: string }> = {
  structural: { name: "Structural", color: "#f59e0b" }, // amber
  piping: { name: "Piping", color: "#22d3ee" }, // cyan
  hvac: { name: "HVAC", color: "#34d399" }, // green
  electrical: { name: "Electrical", color: "#a78bfa" }, // violet
};

export const DISCIPLINE_ORDER: DisciplineKey[] = ["structural", "piping", "hvac", "electrical"];

export function disciplineColor(key: string): string {
  return DISCIPLINE_META[key as DisciplineKey]?.color ?? "#94a3b8";
}

// ── formatting ────────────────────────────────────────────────────────
const _num = (n: number) => (Number.isFinite(n) ? n : 0);

/** Mass/length with one decimal + thousands separators (e.g. 404.9). */
export function fmt1(n: number): string {
  return _num(n).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
/** Rounded integer with thousands separators (e.g. 1,712). */
export function fmt0(n: number): string {
  return Math.round(_num(n)).toLocaleString("en-US");
}
/** A coordinate, two decimals (e.g. 23.14). */
export function fmtCog(n: number): string {
  return _num(n).toFixed(2);
}

// ── derivations (pure; call inside component useMemo, never in a selector) ──
export function sumBy<T>(rows: readonly T[], pick: (r: T) => number): number {
  return rows.reduce((s, r) => s + _num(pick(r)), 0);
}

export interface MassShareSeg {
  key: DisciplineKey;
  name: string;
  color: string;
  mass: number;
  pct: number; // 0..100
}

/** Discipline mass-share segments for the stacked bar + legend. */
export function massShare(stats: ModelStats): MassShareSeg[] {
  const total = stats.disciplines.reduce((s, d) => s + _num(d.mass), 0) || 1;
  return DISCIPLINE_ORDER.map((key) => {
    const d = stats.disciplines.find((x) => x.key === key);
    const mass = d ? _num(d.mass) : 0;
    return { key, name: DISCIPLINE_META[key].name, color: DISCIPLINE_META[key].color, mass, pct: (mass / total) * 100 };
  });
}

export interface CountSummary {
  beams: number;
  plates: number;
  pipeSeg: number;
  ductSeg: number;
  traySeg: number;
}

/** The object counts shown in the compact card's footer row. */
export function countSummary(stats: ModelStats): CountSummary {
  return {
    beams: sumBy(stats.structural.beams, (r) => r.count),
    plates: sumBy(stats.structural.plates, (r) => r.count),
    pipeSeg: sumBy(stats.piping.segments, (r) => r.segments),
    ductSeg: sumBy(stats.hvac.segments, (r) => r.segments),
    traySeg: sumBy(stats.electrical.trays, (r) => r.segments),
  };
}

/** Number of disciplines that actually carry mass/objects. */
export function activeDisciplineCount(stats: ModelStats): number {
  return stats.disciplines.filter((d) => _num(d.mass) > 0 || d.count > 0).length;
}

export const STATS_TABS = [
  { key: "overview", label: "Overview", color: null as string | null },
  { key: "cogs", label: "COGs", color: "var(--ada-accent, #3b82f6)" },
  { key: "structural", label: "Structural", color: DISCIPLINE_META.structural.color },
  { key: "piping", label: "Piping", color: DISCIPLINE_META.piping.color },
  { key: "hvac", label: "HVAC", color: DISCIPLINE_META.hvac.color },
  { key: "electrical", label: "Electrical", color: DISCIPLINE_META.electrical.color },
] as const;

export type StatsTabKey = (typeof STATS_TABS)[number]["key"];
