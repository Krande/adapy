// The active-field arbiter's mode side: who owns the scene colouring right now.
//
// A plugin mode whose whole purpose is its own per-element colouring (an
// engineering-check overlay, a property painter) declares `ownsSceneColor` on
// its PluginModeSpec. While such a mode is active the viewer's FEA field must
// not show underneath it — a field from one analysis read as if it belonged to
// another — and on leaving, the user must find their field exactly as they left
// it, range and legend included. See issue #308.
//
// Core does the suspending, on the mode's declared behalf. A shell only reports
// the transition (`notifyActiveModeSceneColor`); it never touches scene state
// itself, which keeps a shell's "modes change which tools are offered, not what
// is displayed" contract intact.

import { useColorStore } from "@/state/colorLegendStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";

// Loaded on demand: the streaming loader pulls in three + the whole scene
// stack, which neither the boot path nor a unit test should pay for.
async function applyColorsVisible(visible: boolean): Promise<void> {
  try {
    const { setFeaResultColorsVisible } = await import(
      "@/utils/scene/handlers/load_fea_streaming"
    );
    setFeaResultColorsVisible(visible);
  } catch {
    // Only reachable where the scene stack itself cannot load (a non-Vite
    // test runtime); with no scene there is nothing to repaint.
  }
}

/** The one property of a mode this module reads. Matches PluginModeSpec, and a
 * shell's own built-in modes can satisfy it without being plugin modes. */
export interface SceneColorMode {
  id: string;
  ownsSceneColor?: boolean;
}

interface SavedFieldView {
  fieldName: string | null;
  reduction: string;
  stepIndex: number;
  layer: string | null;
  legendShown: boolean;
  legendMin: number;
  legendMax: number;
}

let owner: string | null = null;
let saved: SavedFieldView | null = null;

/** Which mode currently owns the scene colouring, or null. Exposed for tests
 * and for shells that want to render an indicator. */
export function sceneColorOwner(): string | null {
  return owner;
}

/**
 * Report the active mode. Idempotent; call on every mode transition.
 *
 * A mode with `ownsSceneColor` suspends the active FEA field colouring —
 * vertex colours off, legend hidden — without touching the user's own
 * selections or toggles. A mode without it (or `null`, no mode system at all)
 * restores what was suspended: if the owning mode loaded a different field into
 * the buffers, the previously selected field is reloaded; otherwise the
 * colours and legend simply come back. Switching directly between two owning
 * modes keeps the original saved view, so A -> B -> results still restores what
 * the user had before A.
 */
export function notifyActiveModeSceneColor(mode: SceneColorMode | null): void {
  const owns = !!mode?.ownsSceneColor;
  if (owns) {
    if (owner !== null) {
      // Owner-to-owner transition: the saved view is still the one to restore.
      owner = mode!.id;
      return;
    }
    const fea = useFeaAnimationStore.getState();
    const legend = useColorStore.getState();
    saved = {
      fieldName: fea.fieldName ?? null,
      reduction: fea.reduction,
      stepIndex: fea.stepIndex,
      layer: fea.layer ?? null,
      legendShown: legend.showLegend,
      legendMin: legend.min,
      legendMax: legend.max,
    };
    owner = mode!.id;
    // Suppress without recording it as a user preference: the store's
    // `resultColorsVisible` toggle stays whatever the user set, and is
    // consulted again on restore.
    void applyColorsVisible(false);
    legend.setShowLegend(false);
    return;
  }

  if (owner === null) return;
  owner = null;
  const view = saved;
  saved = null;
  if (!view) return;

  const fea = useFeaAnimationStore.getState();
  const legend = useColorStore.getState();

  if (view.fieldName && fea.fieldName !== view.fieldName) {
    // The owning mode loaded its own field into the colour buffers (a property
    // painter does). Reload the user's field; the load rebuilds colours,
    // range and legend, and honours the colour-visibility toggle itself.
    fea.setStepIndex(view.stepIndex);
    if (view.layer) fea.setLayer(view.layer);
    void import("@/utils/scene/fea/resultSelection")
      .then(({ selectFeaResultComponent }) =>
        selectFeaResultComponent(view.fieldName!, view.reduction),
      )
      .catch(() => {
        // The manifest may have been replaced while the mode was active; a
        // vanished field is not an error worth surfacing on a mode switch.
      });
    return;
  }

  // Buffers untouched (an overlay painter, or no field at all): put the
  // visibility and legend back exactly. The store toggle is authoritative for
  // whether colours show; the legend numbers are restored from the snapshot
  // because an owning mode may have driven them through paintField.
  void applyColorsVisible(view.fieldName ? fea.resultColorsVisible : false);
  legend.setMin(view.legendMin);
  legend.setMax(view.legendMax);
  legend.setShowLegend(view.legendShown);
}

/** Test hook: forget any suspended state without side effects. */
export function _resetSceneColorOwnerForTests(): void {
  owner = null;
  saved = null;
}
