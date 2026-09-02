import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { load_fea_streaming } from "@/utils/scene/handlers/load_fea_streaming";
import { availableResultLayers } from "@/utils/scene/fea/resultLayers";

/** Shared action for compact controls and external/docked result trees. */
export async function selectFeaResultComponent(
  fieldName: string,
  component?: string,
): Promise<void> {
  const state = useFeaAnimationStore.getState();
  const { sourceName, manifest } = state;
  if (!sourceName || !manifest) return;
  const field = manifest.fields.find(
    (candidate) => candidate.name_canonical === fieldName,
  );
  if (!field) throw new Error(`Unknown FEA result field ${fieldName}`);
  const reduction =
    component && field.components.includes(component)
      ? component
      : (field.default_view?.reduction ?? field.components[0] ?? "scalar");
  if (field.default_view?.layer) state.setLayer(field.default_view.layer);
  if (field.default_view?.ip_reduction)
    state.setIpReduction(field.default_view.ip_reduction);
  const stepIndex = Math.min(state.stepIndex, Math.max(field.n_steps - 1, 0));
  await load_fea_streaming({
    sourceName,
    manifest,
    fieldName,
    stepIndex,
    reduction,
    displacementScale: state.factor * state.scaleFactor,
    colormap: state.colormap,
  });
}

/** Shared surface/layer action for compact controls and Docked UI plugins. */
export async function selectFeaResultLayer(layer: string): Promise<void> {
  const state = useFeaAnimationStore.getState();
  const { sourceName, manifest, fieldName } = state;
  if (!sourceName || !manifest || !fieldName) return;
  const field = manifest.fields.find(
    (candidate) => candidate.name_canonical === fieldName,
  );
  if (!field) throw new Error(`Unknown FEA result field ${fieldName}`);
  const available = availableResultLayers(field);
  if (!available.includes(layer)) {
    throw new Error(`Layer ${layer} is unavailable for FEA result field ${fieldName}`);
  }
  state.setLayer(layer);
  await load_fea_streaming({
    sourceName,
    manifest,
    fieldName,
    stepIndex: Math.min(state.stepIndex, Math.max(field.n_steps - 1, 0)),
    reduction: state.reduction,
    displacementScale: state.factor * state.scaleFactor,
    colormap: state.colormap,
  });
}
