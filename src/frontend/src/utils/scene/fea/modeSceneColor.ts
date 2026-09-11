// The active-field arbiter's mode side: who owns the scene colouring right now.
//
// A plugin mode whose whole purpose is its own per-element colouring (an
// engineering-check overlay, a property painter) declares `ownsSceneColor` on
// its PluginModeSpec. While such a mode is active the viewer's FEA field must
// not show underneath it — a field from one analysis read as if it belonged to
// another — and on leaving, the user must find their field exactly as they left
// it, range and legend included. See issue #308.
//
// The same promise runs the other way. Every mode is left as it was found: an
// owning mode that painted something of its own gets that back when you return
// to it, rather than being suspended again as though you had never been there.
// A mode that colours entirely outside core has painted nothing as far as the
// arbiter knows, and is suspended on every entry, as on the first.
//
// Ownership is explicit state, held in `useSceneColorOwnerStore` as a stack:
// `results` (the user's own view) at the bottom, the active owning mode on top.
// Entering an owning mode pushes it and records what the owner beneath was
// showing; leaving pops it and puts that back. Every paint and every loader
// landing is tagged with the owner that asked for it - `paintField` tags the
// active mode, the loader tags a load when it is REQUESTED (see
// `requestingSceneColorOwner`) - and the tag decides whose view it is: the
// mode's own painting if the mode on top asked for it, otherwise the user's
// result, set aside under the mode and restored when it leaves. A load already
// in flight when a mode is entered therefore lands as the user's, never as the
// mode's. This module only snapshots the screen for the store and applies what
// the store hands back; it holds no state of its own.
//
// Nothing is read off the screen: an owner keeps its colouring on leaving only
// when it reported a paint. That is safe because every production path that
// changes the field runs through the FEA loader, which tags each landing with
// the owner that asked for it (`requestingSceneColorOwner` at request time),
// and the one painter that does not touch the field - the legend-only
// `paintField` - reports itself through `noteOwnerPainted`.
//
// Core does the suspending, on the mode's declared behalf. A shell only reports
// the transition (`notifyActiveModeSceneColor`); it never touches scene state
// itself, which keeps a shell's "modes change which tools are offered, not what
// is displayed" contract intact.

import { useColorStore } from "@/state/colorLegendStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import {
  RESULTS_OWNER,
  useSceneColorOwnerStore,
  type SceneColorView,
} from "@/state/sceneColorOwnerStore";

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

/** Snapshot what is on screen now. */
function snapshot(): SceneColorView {
  const fea = useFeaAnimationStore.getState();
  const legend = useColorStore.getState();
  return {
    fieldName: fea.fieldName ?? null,
    reduction: fea.reduction,
    stepIndex: fea.stepIndex,
    layer: fea.layer ?? null,
    legendShown: legend.showLegend,
    legendMin: legend.min,
    legendMax: legend.max,
  };
}

/** Put a snapshot back on screen: its field, its legend, its colours. */
function restore(view: SceneColorView): void {
  const fea = useFeaAnimationStore.getState();
  const legend = useColorStore.getState();

  if (view.fieldName && fea.fieldName !== view.fieldName) {
    // A different field is in the colour buffers. Reload the saved one; the load
    // rebuilds colours, range and legend, and honours the visibility toggle.
    // The reload is requested by whoever is now on top of the stack, so it
    // lands tagged as theirs.
    fea.setStepIndex(view.stepIndex);
    if (view.layer) fea.setLayer(view.layer);
    void import("@/utils/scene/fea/resultSelection")
      .then(({ selectFeaResultComponent }) =>
        selectFeaResultComponent(view.fieldName!, view.reduction),
      )
      .catch(() => {
        // The manifest may have been replaced while the mode was away; a
        // vanished field is not an error worth surfacing on a mode switch.
      });
    return;
  }

  // Buffers already hold it: put the visibility and legend back exactly.
  void applyColorsVisible(view.fieldName ? fea.resultColorsVisible : false);
  legend.setMin(view.legendMin);
  legend.setMax(view.legendMax);
  legend.setShowLegend(view.legendShown);
}

/** Suspend: no field colouring, no legend, no change to the user's toggles. */
function suspend(): void {
  // Suppressed without being recorded as a user preference: the store's
  // `resultColorsVisible` toggle stays whatever the user set, and is consulted
  // again on restore.
  void applyColorsVisible(false);
  useColorStore.getState().setShowLegend(false);
}

/** Which mode currently owns the scene colouring, or null. Exposed for tests
 * and for shells that want to render an indicator. */
export function sceneColorOwner(): string | null {
  const owner = useSceneColorOwnerStore.getState().activeOwner();
  return owner === RESULTS_OWNER ? null : owner;
}

/**
 * Report the active mode. Idempotent; call on every mode transition.
 *
 * A mode with `ownsSceneColor` is pushed onto the owner stack. On its first
 * entry it suspends the active FEA field colouring — vertex colours off, legend
 * hidden — without touching the user's own selections or toggles; on a later
 * entry after it painted something of its own, that painting comes back
 * instead. A mode without it (or `null`, no mode system at all) pops the
 * owning mode and restores what the owner beneath was showing: if a different
 * field is in the buffers, the saved field is reloaded; otherwise the colours
 * and legend simply come back. Switching directly between two owning modes
 * swaps the top of the stack, so A -> B -> results still restores what the
 * user had before A.
 */
export function notifyActiveModeSceneColor(mode: SceneColorMode | null): void {
  const store = useSceneColorOwnerStore.getState();
  const active = store.activeOwner();

  if (mode?.ownsSceneColor) {
    if (active === mode.id) return;
    const entry =
      active === RESULTS_OWNER
        ? store.push(mode.id, snapshot())
        : store.switchTop(mode.id, snapshot());
    if (entry.view) restore(entry.view);
    else suspend();
    return;
  }

  if (active === RESULTS_OWNER) return;
  const popped = store.pop(snapshot());
  if (popped?.below.view) restore(popped.below.view);
}

/**
 * Report a paint through core that went neither through the loader nor changed
 * the field: the legend-only painter (`paintField` in the plugin context)
 * drives the shared legend off its own range and leaves the buffers alone.
 * Tagged with the active owning mode unless the caller names one; the store
 * counts it only for the owner on top, so outside an owning mode it is a
 * no-op. Its legend is the mode's view, and comes back on re-entry like a
 * loader repaint would.
 */
export function noteOwnerPainted(owner?: string): void {
  const store = useSceneColorOwnerStore.getState();
  store.markPainted(owner ?? store.activeOwner());
}

/**
 * The owner a load of `source` requested right now belongs to. The loader
 * takes this when a load is requested and hands it back to
 * `noteFieldSourceLoaded` when the load lands, so the tag survives a mode
 * change in between: a result picked before an owning mode was entered lands
 * as the user's result, set aside under the mode, not as the mode's painting.
 *
 * The active owning mode owns a repaint of the source already on screen (that
 * is how a property painter colours through the loader); everything else -
 * no owning mode, a first load, another model - is the user's result.
 */
export function requestingSceneColorOwner(source: string | null): string {
  return useSceneColorOwnerStore.getState().requestingOwner(source);
}

/**
 * Report that the FEA loader has just put a field on screen for `source` on
 * behalf of `owner` (from `requestingSceneColorOwner` at request time; a caller
 * without a tag gets the owner a request made now would have).
 *
 * Under an owning mode, a load the mode asked for is its own painting and is
 * left alone. Any other load is the user's result arriving under the mode -
 * the page opened straight into the mode (a restored session, a `?mode=`
 * link) and the model loaded afterwards, or the user opened another model
 * while the mode was active - so it is recorded as the view to put back on
 * leaving, and set aside now. The capacity overlay used to sit on top of a
 * displacement field, its legend floating beside it, because the load switched
 * the field's colours and legend on under the mode.
 */
export function noteFieldSourceLoaded(source: string | null, owner?: string): void {
  const store = useSceneColorOwnerStore.getState();
  const tag = owner ?? store.requestingOwner(source);
  if (store.noteLoad(source, tag, snapshot())) suspend();
}

/**
 * Report that the FEA loader has cleared its model (`clearActiveFeaStreaming`).
 *
 * Every saved view referred to the model that is gone, so they go with it, and
 * whatever comes next is a NEW source even when it is the same file: clear a
 * model and reopen it inside an owning mode, and the load is the user's result
 * arriving under the mode, not the mode repainting. Without this the old source
 * name survived the clear and the reopen was classed as the mode's own repaint,
 * showing the field under the overlay.
 */
export function noteFieldSourceCleared(): void {
  useSceneColorOwnerStore.getState().clearViews();
}

/** Test hook: forget any suspended state without side effects. */
export function _resetSceneColorOwnerForTests(): void {
  useSceneColorOwnerStore.getState().reset();
}
