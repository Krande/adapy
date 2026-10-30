import type {FeaManifest} from "@/services/api/fea";

/** ``INSTANCE.label`` for an element the bake moved into an instance's own label block (see
 *  ``mesh.element_instances``); null for an element that kept its own number. */
export function movedElementName(manifest: FeaManifest, label: number): string | null {
    const moved = (manifest.mesh?.element_instances ?? []).filter((i) => i.offset > 0);
    const owner = moved.filter((i) => i.offset <= label).sort((a, b) => b.offset - a.offset)[0];
    return owner ? `${owner.name}.${label - owner.offset}` : null;
}
