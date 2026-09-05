import type { FeaManifestField } from "@/services/viewerApi";

/** Unit for the active component/reduction, retaining legacy field-level units. */
export function selectedResultUnit(
  field: FeaManifestField | null | undefined,
  reduction: string,
): string {
  if (!field) return "";
  const componentIndex = field.components.indexOf(reduction);
  if (componentIndex >= 0) {
    const componentUnit = field.component_units?.[componentIndex];
    if (componentUnit) return componentUnit;
  }
  return field.unit ?? "";
}

/** Fixed manifest range used by the renderer for the selected component. */
export function selectedResultRange(
  field: FeaManifestField | null | undefined,
  reduction: string,
): [number, number] {
  if (!field) return [0, 1];
  const selected = field.scalar_range[reduction];
  if (selected) return [selected[0], selected[1]];
  const fallback = field.components.length > 0
    ? field.scalar_range[field.components[0]]
    : undefined;
  return fallback ? [fallback[0], fallback[1]] : [0, 1];
}
