import type { FeaManifestField } from "@/services/viewerApi";

export interface FeaResultAttributeGroup {
  label: string;
  field: FeaManifestField;
}

export interface FeaResultPositionGroup {
  label: string;
  attributes: FeaResultAttributeGroup[];
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
  const ungrouped: FeaManifestField[] = [];
  for (const field of fields) {
    const path = field.group_path;
    if (!path || path.length < 2) {
      ungrouped.push(field);
      continue;
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
