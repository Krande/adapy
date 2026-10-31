// What the comparison gallery shows of one element, as plain functions (tested without a DOM).

import type { CompareItem, CompareVerdict } from "@/services/api/geometryCompare";

export const GALLERY_VERDICTS: CompareVerdict[] = ["fail", "uncertain", "missing"];

export const VERDICT_TONE: Record<CompareVerdict, string> = {
  pass: "text-green-300",
  uncertain: "text-amber-300",
  fail: "text-red-300",
  missing: "text-gray-400",
};

/** The metrics a gallery may sort by, worst first, with their labels. */
export const SORTS: { value: string; label: string }[] = [
  { value: "", label: "Newest first" },
  { value: "box_delta_mm", label: "Box delta" },
  { value: "hausdorff_mm", label: "Surface distance" },
  { value: "p95_mm", label: "Surface p95" },
  { value: "volume_ratio", label: "Volume ratio" },
];

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The metric lines under a pair, most telling first; what was not measured is left out. */
export function metricLines(item: CompareItem): string[] {
  const m = item.metrics ?? {};
  const out: string[] = [];
  const box = num(m.box_delta_mm);
  if (box != null) out.push(`box Δ ${box.toFixed(1)} mm`);
  const h = num(m.hausdorff_mm);
  if (h != null) out.push(`surface ≤ ${h.toFixed(1)} mm (p95 ${(num(m.p95_mm) ?? 0).toFixed(1)})`);
  const vr = num(m.volume_ratio);
  if (vr != null) out.push(`volume ×${vr.toFixed(3)}`);
  const tr = num(m.triangle_ratio);
  if (tr != null) out.push(`triangles ×${tr.toFixed(2)}`);
  if (Array.isArray(m.open) && m.open.some(Boolean)) out.push("a mesh is not closed");
  if (item.verdict === "missing") out.push(item.a ? `only in ${item.provider_a}` : `only in ${item.provider_b}`);
  return out;
}

/** The published node (else subject) the element was built under on one side -- what to find it by
 *  in that provider's tree in the Sources tab. */
export function nodeOf(item: CompareItem, side: "a" | "b"): string | null {
  const s = side === "a" ? item.a : item.b;
  const node = s?.node ?? s?.subject;
  return typeof node === "string" && node ? node : null;
}
