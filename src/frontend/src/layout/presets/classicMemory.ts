// The classic preset's memory of open panels: what entering a mode opens (what
// was open when it was last left, else its starting list), the global panels one
// state for every mode, and what leaving a mode or a whole shell closes. No JSX,
// so `node --test` drives it directly.

import {CLASSIC_PRESET_ID, type LayoutDoc} from "../layout";
import {layoutStore} from "../layoutStore";
import type {PanelId} from "../panel";
import {dockInGridDoc, floatPanel, gridOf, updateGrid} from "../reducers";
import {listPanels} from "../registry";
import {VIEWPORT_NODE, findNode, gridPanels, groupOf, setFixedPx} from "../splitTree";
import {CLASSIC_ZONE_DEFS} from "./classicRules";
import {arrangementOf, classicDoc, pinnedPanels, placeOf, servedClassicShell, updateClassic} from "./classicShell";

/** The served shell's global panels (`ClassicShell.globalPanels`). */
function globalPanelIds(): Set<PanelId> {
  return new Set(servedClassicShell()?.globalPanels ?? []);
}

/** Every panel a document places: in its grid, or in a window. */
function placedPanels(doc: LayoutDoc): PanelId[] {
  return [...new Set([...gridPanels(gridOf(doc)), ...Object.keys(doc.floats)])];
}

/** What the mode entered opens: what was open when it was last left, else its starting list. */
function openingPanels(): readonly PanelId[] {
  return (servedClassicShell() ? classicDoc().open : undefined) ?? arrangementOf().open ?? [];
}

/**
 * Open the mode's panels: on every load, and on entering a mode. A mode recorded
 * as it was left (`LayoutDoc.open`) opens what was open then and closes the rest it
 * places; one never recorded opens its starting list. A global panel is left as it
 * is, at its global state (`applyGlobalPanels`). Whether a panel is open is its own
 * flag, which a reload resets, while where it shows and what was open are the saved
 * document. Call after the layout's panel controller is installed.
 */
export function openStartingPanels(
  reveal: (id: PanelId) => boolean,
  hide?: (id: PanelId) => boolean,
  isOpen?: (id: PanelId) => boolean,
): void {
  const shell = servedClassicShell();
  const global = globalPanelIds();
  const remembered = shell ? classicDoc().open : undefined;
  if (remembered && hide && isOpen) {
    const want = new Set(remembered);
    for (const id of placedPanels(classicDoc())) {
      if (!global.has(id) && !want.has(id) && isOpen(id)) hide(id);
    }
    for (const id of remembered) if (!global.has(id)) reveal(id);
  } else {
    const known = shell ? layoutStore.getState().prefs.globalPanels?.[shell.id] : undefined;
    for (const id of arrangementOf().open ?? []) if (!global.has(id) || !known) reveal(id);
  }
  if (hide) applyGlobalPanels(reveal, hide);
}

/**
 * Put the global panels in the state recorded for every mode: open or closed, and
 * the docked group each is in at the recorded size. Nothing recorded yet: left as
 * the mode's starting list made them.
 */
export function applyGlobalPanels(reveal: (id: PanelId) => boolean, hide: (id: PanelId) => boolean): void {
  const shell = servedClassicShell();
  if (!shell) return;
  const state = layoutStore.getState().prefs.globalPanels?.[shell.id];
  if (!state) return;
  const open = new Set(state.open);
  for (const id of globalPanelIds()) {
    if (open.has(id)) reveal(id);
    else hide(id);
    const px = state.px[id];
    const group = groupOf(gridOf(classicDoc()), id);
    if (typeof px === "number" && group?.fixed && group.fixed.px !== px) {
      updateClassic((d) => updateGrid(d, (g) => setFixedPx(g, group.id, px)));
    }
  }
}

const sameList = (a: readonly string[] | undefined, b: readonly string[]) =>
  !!a && a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * Record the mode as it is now: which of the panels it places are open (into its
 * document, `LayoutDoc.open`), and the global panels' state (open, and their docked
 * size) for every mode. Run on any change to a panel's open state or the document;
 * writes only what changed.
 */
export function recordOpenPanels(isOpen: (id: PanelId) => boolean): void {
  const shell = servedClassicShell();
  if (!shell) return;
  const global = globalPanelIds();
  const doc = classicDoc();
  const open = placedPanels(doc).filter((id) => !global.has(id) && isOpen(id)).sort();
  if (!sameList(doc.open, open)) updateClassic((d) => ({...d, open}));
  if (!global.size) return;
  const before = layoutStore.getState().prefs.globalPanels?.[shell.id];
  const px: Record<PanelId, number> = {...(before?.px ?? {})};
  for (const id of global) {
    const fixed = groupOf(gridOf(doc), id)?.fixed;
    if (fixed && typeof fixed.px === "number") px[id] = fixed.px;
  }
  const gOpen = [...global].filter((id) => isOpen(id)).sort();
  const samePx =
    !!before && Object.keys(px).length === Object.keys(before.px).length && Object.entries(px).every(([k, v]) => before.px[k] === v);
  if (!before || !sameList(before.open, gOpen) || !samePx) layoutStore.getState().setGlobalPanels(shell.id, {open: gOpen, px});
}

/**
 * Leaving a whole UI shell (an in-place switch, e.g. Classic <-> Desktop): every open panel closes,
 * pinned ones too, except the starting panels of the shell (and mode) switched to, which it opens
 * anyway (`openStartingPanels`, after this). So the shell switched to starts as a fresh page of it
 * would. Without it a panel open in one shell stayed open in the other: the classic Options card
 * floating over the Desktop UI, the Desktop UI's tree and properties left open in the classic one.
 * Call after the switch, so the arrangement is the new shell's.
 */
export function leaveShell(hide: (id: PanelId) => boolean, isOpen: (id: PanelId) => boolean): void {
  const opening = new Set(arrangementOf().open ?? []);
  for (const {id} of listPanels()) {
    if (!opening.has(id) && isOpen(id)) hide(id);
  }
}

/**
 * Leaving a mode: the panels its arrangement placed (in the grid or a window)
 * go with it, unless the mode entered places or opens them too. A panel open in
 * its home stays open. Call with the context left, after the switch.
 */
export function leaveContext(left: string, hide: (id: PanelId) => boolean, isOpen: (id: PanelId) => boolean): void {
  const before = layoutStore.getState().doc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, left);
  const now = classicDoc();
  const opening = new Set(openingPanels());
  const pinned = new Set(pinnedPanels());
  const global = globalPanelIds();
  const placedBefore = [...gridPanels(gridOf(before)), ...Object.keys(before.floats)];
  for (const id of placedBefore) {
    // A global panel is one panel for every mode: the mode changing does not close it.
    if (global.has(id)) continue;
    if (placeOf(now, id) !== "home") continue;
    if (pinned.has(id)) {
      carryPinned(before, id);
      continue;
    }
    if (opening.has(id) || !isOpen(id)) continue;
    hide(id);
  }
}

/**
 * A pinned panel the mode entered does not place: placed as it was, in a window
 * at the same rect, or in the group of the same id, else at the view's right edge.
 */
function carryPinned(before: LayoutDoc, id: PanelId): void {
  const f = before.floats[id];
  if (f) {
    updateClassic((d) => floatPanel(d, id, {x: f.x, y: f.y, w: f.w, h: f.h}));
    return;
  }
  const from = groupOf(gridOf(before), id);
  updateClassic((d) => {
    const into = from && findNode(gridOf(d), from.id);
    return dockInGridDoc(d, id, into && into.kind === "group" ? {node: from!.id, edge: "center"} : {node: VIEWPORT_NODE, edge: "right"});
  });
}

