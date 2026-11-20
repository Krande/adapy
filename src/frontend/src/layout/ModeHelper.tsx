// The classic UI's mode helper: three headings (Topology, FEM, Construction), each lit while its
// kind of content is on screen, and under Topology a dropdown for the input / output comparison.
// It switches no mode. It starts fixed at the top middle of the view with no title bar; a right
// click offers to undock it into the classic layout (a window, an edge of the view), and from
// there back to the top. Core's own UI only (`modeHelperOffered`).

import React from "react";
import {createPortal} from "react-dom";
import {useShallow} from "zustand/react/shallow";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModeHelperStore} from "@/state/modeHelperStore";
import {hasFemConcepts} from "@/components/simulation/simPanelTabs";
import {hasEmbeddedDoc} from "@/state/cellBuilderStore";

import {CORE_PANEL_IDS} from "./corePanelIds";
import {setCustomisablePanels, useCustomisablePanels} from "./customisable";
import {COMPARISONS, HELPER_MODES, activeModes, applyComparison, comparisonOf, modeHelperOffered, type Comparison} from "./modeHelperRules";
import type {PanelMenuItem} from "./panel";
import {getPanel} from "./registry";
import {moveAction, shownPlace, useClassicDoc} from "./presets/classicPlace";
import {useIsNarrow} from "@/utils/useIsMobile";

/** What the headings read, subscribed (the rule is `activeModes`). */
function useActiveModes() {
  const i = useCellBuilderStore(
    useShallow((s) => ({builderLoaded: s.active !== null || hasEmbeddedDoc(s), simulationLoaded: s.resultSourceName !== null, detailLoaded: s.detailSourceName !== null})),
  );
  const fea = useFeaAnimationStore(useShallow((s) => ({manifest: s.manifest, sessionActive: s.sessionActive})));
  const femHasConcepts = useFemConceptsStore(hasFemConcepts);
  return activeModes({...i, ...fea, femHasConcepts});
}

const NO_MODEL = "No procedural model is open";

/** The helper's content: the headings and the comparison dropdown. Frameless; the host gives it a card or a window. */
export function ModeHelperBody() {
  const active = useActiveModes();
  const loaded = active.has("topology");
  const value = useCellBuilderStore((s) => comparisonOf(s));
  const [menu, setMenu] = React.useState<{x: number; y: number} | null>(null);
  return (
    <div
      role="group"
      aria-label="Mode helper"
      data-mode-helper=""
      className="flex flex-col gap-1.5 text-xs text-gray-200 select-none"
      onContextMenu={(e) => {
        e.preventDefault();
        setMenu({x: e.clientX, y: e.clientY});
      }}
    >
      <ul className="flex flex-row items-center gap-1.5 m-0 p-0 list-none">
        {HELPER_MODES.map((m) => {
          const on = active.has(m.id);
          return (
            <li
              key={m.id}
              data-mode={m.id}
              data-active={on ? "true" : "false"}
              aria-label={`${m.label}: ${on ? "active" : "inactive"}`}
              className={
                "flex items-center gap-1.5 rounded px-2 py-1 font-medium border " +
                (on ? "bg-emerald-500/25 border-emerald-400 text-emerald-200" : "bg-transparent border-gray-700 text-gray-500")
              }
            >
              <span aria-hidden="true" className={"inline-block h-2 w-2 rounded-full " + (on ? "bg-emerald-400" : "border border-gray-600")} />
              {m.label}
            </li>
          );
        })}
      </ul>
      <label className="flex items-center gap-1.5" title={loaded ? "Topology input / output comparison" : NO_MODEL}>
        <span className="text-gray-400">Compare</span>
        <select
          aria-label="Topology input / output comparison"
          title={loaded ? "Topology input / output comparison" : NO_MODEL}
          disabled={!loaded}
          value={value}
          onChange={(e) => void applyComparison(e.target.value as Comparison, useCellBuilderStore.getState())}
          className="rounded border border-gray-600 bg-gray-900 px-1 py-0.5 text-xs text-gray-100 disabled:opacity-50"
        >
          {COMPARISONS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      {menu && <ModeHelperMenu at={menu} onClose={() => setMenu(null)} />}
    </div>
  );
}

/** The right-click menu: the layout's own moves (float, dock at an edge, stack), the way home relabelled "Back to the top". */
function ModeHelperMenu({at, onClose}: {at: {x: number; y: number}; onClose: () => void}) {
  const doc = useClassicDoc();
  const customisable = useCustomisablePanels();
  const narrow = useIsNarrow();
  const panel = getPanel(CORE_PANEL_IDS.modeHelper);
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const away = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", key, true);
    ref.current?.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", key, true);
    };
  }, [onClose]);
  if (!panel) return null;
  const items: PanelMenuItem[] = (moveAction(panel, doc).menu ?? []).filter((m) => /^(float|dock-|stack-|home)/.test(m.id));
  const home = shownPlace(doc, panel.id, customisable, narrow) === "home";
  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label="Mode helper"
      style={{left: Math.min(at.x, window.innerWidth - 220), top: Math.min(at.y, window.innerHeight - 40 * (items.length + 1))}}
      className="fixed z-[200] min-w-[200px] rounded-md border border-gray-600 bg-gray-800 py-1 text-xs text-gray-100 shadow-xl"
    >
      {!customisable && (
        <div className="px-3 py-1 text-gray-400">Undocking turns on Options, Customisable panels</div>
      )}
      {items.map((m) => (
        <button
          key={m.id}
          role="menuitem"
          disabled={m.disabled && customisable}
          onClick={() => {
            // The layout's windows and groups exist with the setting on.
            if (!customisable && m.id !== "home") setCustomisablePanels(true);
            onClose();
            m.run();
          }}
          className="block w-full px-3 py-1 text-left hover:bg-gray-700 disabled:opacity-50 disabled:hover:bg-transparent"
        >
          {m.label}
          {m.current ? " (here)" : ""}
        </button>
      ))}
      {home && customisable && items.length === 0 && <div className="px-3 py-1 text-gray-400">Nothing to undock to</div>}
    </div>,
    document.body,
  );
}

/**
 * Where the helper is at home: fixed at the top middle of the view, a compact dark card with no
 * title bar. Drawn while the helper is on and the layout has not undocked it.
 */
export function ModeHelperHome() {
  const on = useModeHelperStore((s) => s.on);
  const doc = useClassicDoc();
  const customisable = useCustomisablePanels();
  const narrow = useIsNarrow();
  if (!on || !modeHelperOffered()) return null;
  if (shownPlace(doc, CORE_PANEL_IDS.modeHelper, customisable, narrow) !== "home") return null;
  return (
    <div
      className="pointer-events-auto fixed left-1/2 top-2 z-[40] -translate-x-1/2 rounded-md border border-gray-600 bg-gray-800/95 px-2 py-1.5 shadow-lg"
      data-mode-helper-home=""
    >
      <ModeHelperBody />
    </div>
  );
}
