import type { FeaManifestField } from "@/services/viewerApi";

/** Surface/layer choices in stable physical order. Selectable surfaces omit
 * ``all`` because reducing top and bottom together is not an exact surface. */
export function availableResultLayers(field: FeaManifestField): string[] {
  const layers = new Set<string>();
  for (const bucket of field.per_type ?? []) {
    for (const point of bucket.ip_layout ?? []) {
      if (point.layer) layers.add(point.layer);
    }
  }
  const layerRank: Record<string, number> = { top: 0, mid: 1, bottom: 2 };
  const rank = (value: string): number => layerRank[value] ?? 3;
  const out = Array.from(layers).sort(
    (left, right) => rank(left) - rank(right) || left.localeCompare(right),
  );
  if (out.length > 0 && field.surface !== "selectable") out.push("all");
  return out;
}
