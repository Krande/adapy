import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { load_fea_streaming } from "@/utils/scene/handlers/load_fea_streaming";
import { availableResultLayers } from "@/utils/scene/fea/resultLayers";
import type { ContourSettings } from "@/utils/scene/fea/contourScale";

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
  const magnitudeOffered =
    !field.group_path && field.kind.startsWith("vector");
  const reduction =
    component &&
    (field.components.includes(component) ||
      // "magnitude" is a pseudo-component, valid exactly where the pickers
      // offer it (an ungrouped vector field). Rejecting it here made a
      // restore of a magnitude view silently land on the first component.
      (component === "magnitude" && magnitudeOffered))
      ? component
      : (field.default_view?.reduction ?? field.components[0] ?? "scalar");
  if (field.surface_variants?.length) {
    state.setLayer(field.surface || field.surface_variants[0].surface);
  } else if (field.default_view?.layer) {
    state.setLayer(field.default_view.layer);
  }
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
  const variantName = field.surface_variants?.find(
    (variant) => variant.surface === layer,
  )?.field_name;
  const targetFieldName = variantName ?? fieldName;
  const targetField = manifest.fields.find(
    (candidate) => candidate.name_canonical === targetFieldName,
  ) ?? field;
  state.setLayer(layer);
  await load_fea_streaming({
    sourceName,
    manifest,
    fieldName: targetFieldName,
    stepIndex: Math.min(state.stepIndex, Math.max(targetField.n_steps - 1, 0)),
    reduction: targetField.components.includes(state.reduction)
      ? state.reduction
      : (targetField.default_view?.reduction ?? targetField.components[0] ?? "scalar"),
    displacementScale: state.factor * state.scaleFactor,
    colormap: state.colormap,
  });
}

/**
 * Change how the colour scale is drawn, and repaint.
 *
 * The scale settings live in the store, but nothing on screen reads them again
 * until a field is applied — so a dialog that only wrote to the store would move
 * the legend and leave the model in its old colours. One action, called by every
 * control that can change the scale, so the two can never drift apart.
 *
 * No fetch: the field blob is cached, and re-applying the current (field, step,
 * reduction) is CPU work over data already in memory. The step and the sweep
 * slider are untouched, which is what makes this safe to call from a live dialog
 * on every keystroke.
 */
export async function applyContourSettings(
  next: Partial<ContourSettings>,
): Promise<void> {
  const state = useFeaAnimationStore.getState();
  if (next.levels !== undefined) state.setContourLevels(next.levels);
  if (next.min !== undefined || next.max !== undefined) {
    const current = useFeaAnimationStore.getState().contour;
    state.setContourBounds(
      next.min !== undefined ? next.min : current.min,
      next.max !== undefined ? next.max : current.max,
    );
  }
  const after = useFeaAnimationStore.getState();
  const { sourceName, manifest, fieldName } = after;
  if (!sourceName || !manifest || !fieldName) return;
  await load_fea_streaming({
    sourceName,
    manifest,
    fieldName,
    stepIndex: after.stepIndex,
    reduction: after.reduction,
    displacementScale: after.factor * after.scaleFactor,
    colormap: after.colormap,
  });
}

/** Back to the field's own extremes, and repaint. */
export async function resetContourBounds(): Promise<void> {
  await applyContourSettings({ min: null, max: null });
}
