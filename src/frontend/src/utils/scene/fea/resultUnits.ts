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
