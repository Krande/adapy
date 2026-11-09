// What the loaded result's view offers outside the Simulation panel: the colour legend, the FE
// model (FEM concepts) tab, the result data dock. The Simulation panel's own controls and core's
// commands (`core:color-legend`, `core:fem-concepts`, `core:data-dock`) both call these, so the
// two cannot drift. Light on purpose (stores and the panel intents): no scene code.

import {MODEL_TAB} from "@/components/simulation/simPanelTabs";
import {useColorStore} from "@/state/colorLegendStore";
import {openSimulationPanel} from "@/state/simPanelStore";
import {useTableNavStore} from "@/state/tableNavStore";

import {CORE_PANEL_IDS, toggle} from "./panelActions";
import {isMirroredWindow} from "./sceneActions";

/**
 * The result data table, opened and closed as the panel it is (`core:fea-table`). In a pop-out
 * window the table is a section of the Simulation panel there (its flag is this window's own);
 * the panel's own placement is the main window's.
 */
export function toggleDataTable(): void {
  if (isMirroredWindow()) useTableNavStore.getState().togglePanel();
  else void toggle(CORE_PANEL_IDS.feaTable);
}

/** Show or hide the colour legend (the Options panel's "Show Color Legend"). */
export function toggleColorLegend(): void {
  const s = useColorStore.getState();
  s.setShowLegend(!s.showLegend);
}

/** Open the Simulation panel on its FE model tab (the FEM concepts: masses, supports, constraints, load scenarios). */
export function openFemConcepts(): void {
  openSimulationPanel(MODEL_TAB);
}
