import type { FeaManifestField } from "@/services/viewerApi";

export interface FeaResultAttributeGroup {
  label: string;
  field: FeaManifestField;
}

export interface FeaResultPositionGroup {
  label: string;
  attributes: FeaResultAttributeGroup[];
}

/** The model-property fields (category "property": superelement, material, section,
 * thickness), each with a readable label, for the picker's own "Model" group: colouring the
 * model by what its elements ARE, kept apart from the results. The label is the property's
 * group name ("SUPERELEMENT") in title case, or the field name without its namespace. */
export function buildFeaPropertyFields(fields: FeaManifestField[]): FeaResultAttributeGroup[] {
  const out: FeaResultAttributeGroup[] = [];
  for (const field of fields) {
    if (field.category !== "property") continue;
    const raw = field.group_path?.[field.group_path.length - 1] ?? field.name_canonical.replace(/^props\./, "");
    const words = raw.replace(/[_\s]+/g, " ").trim().toLowerCase();
    out.push({ label: words.charAt(0).toUpperCase() + words.slice(1), field });
  }
  return out;
}

/** Build a stable Position -> Attribute hierarchy from optional manifest
 * metadata. Fields without hierarchy metadata are returned in ``ungrouped``
 * so older/non-Sesam manifests keep their flat-picker path. */
export function buildFeaResultHierarchy(fields: FeaManifestField[]): {
  positions: FeaResultPositionGroup[];
  ungrouped: FeaManifestField[];
} {
  const positions: FeaResultPositionGroup[] = [];
  const byPosition = new Map<string, FeaResultPositionGroup>();
  const seenSurfaceSemantics = new Set<string>();
  const ungrouped: FeaManifestField[] = [];
  for (const field of fields) {
    // Model-property fields are input data, not results; a properties/inspect
    // panel lists the category, the results tree and pickers never do.
    if (field.category === "property") continue;
    const path = field.group_path;
    if (!path || path.length < 2) {
      ungrouped.push(field);
      continue;
    }
    if (field.surface_variants?.length && field.semantic_key) {
      if (seenSurfaceSemantics.has(field.semantic_key)) continue;
      seenSurfaceSemantics.add(field.semantic_key);
    }
    const positionLabel = path[0];
    const attributeLabel = path.slice(1).join(" / ");
    let position = byPosition.get(positionLabel);
    if (!position) {
      position = { label: positionLabel, attributes: [] };
      byPosition.set(positionLabel, position);
      positions.push(position);
    }
    position.attributes.push({ label: attributeLabel, field });
  }
  return { positions, ungrouped };
}
