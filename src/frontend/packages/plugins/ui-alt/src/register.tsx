// Reference UI-SHELL plugin — the template an out-of-tree alternative viewer UI
// copies. Where the `demo` plugin proves the in-UI slots (panels, buttons,
// colour fields), this one proves the other axis: a UI of its own.
//
// The shape a real alternative-UI repo takes:
//
//   my-adapy-ui/                     <- its own git repo, its own release cycle
//     package.json                   <- name: "@adapy-plugins/my-ui"
//     plugin.manifest.json
//     src/register.tsx               <- exactly this file's shape
//     src/**                         <- the shell's own chrome and panels
//
// At image-build time CI clones it to `src/frontend/packages/plugins/my-ui/`,
// sets `EXTRA_PLUGINS_ENABLE=my-ui` (so `gen:plugins` registers it) and
// `UI_DEFAULT=my-ui` (so the image boots into it). adapy itself never names the
// repo — see deploy/Dockerfile.viewer.
//
// The shell is COMPOSED: it gives no `load`, so core mounts its own root
// (routes, sign-in, the viewer provider) and its layout of floating, dockable
// panels, starting from `layout.grid`, under the shell's `chrome.header`. The
// user can still rearrange everything, and their arrangement is kept for this
// shell. A shell that needs a root of its own gives `load` instead.
//
// The UI imports core through ONE named surface — `@/viewer-core` (+ its
// `/app`, `/scene`, `/plugins` and `/layout` entry points). The fence is
// enforced by src/__tests__/plugins/viewerCoreFacade.test.ts.

import { CORE_PANEL_IDS, registerPlugin, type GridNode } from "@/viewer-core";

// The tree on the left; the selected object over the scene on the right.
const GRID: GridNode = {
  kind: "split",
  id: "s1",
  dir: "row",
  sizes: [0.2, 0.55, 0.25],
  children: [
    { kind: "group", id: "g1", panels: [CORE_PANEL_IDS.tree], active: CORE_PANEL_IDS.tree },
    { kind: "viewport" },
    {
      kind: "split",
      id: "s2",
      dir: "col",
      sizes: [0.5, 0.5],
      children: [
        { kind: "group", id: "g2", panels: [CORE_PANEL_IDS.objectInfo], active: CORE_PANEL_IDS.objectInfo },
        { kind: "group", id: "g3", panels: [CORE_PANEL_IDS.scene, CORE_PANEL_IDS.storage], active: CORE_PANEL_IDS.scene },
      ],
    },
  ],
};

// The Results mode: the tree, and the simulation controls under the view.
const RESULTS_GRID: GridNode = {
  kind: "split",
  id: "s1",
  dir: "row",
  sizes: [0.2, 0.8],
  children: [
    { kind: "group", id: "g1", panels: [CORE_PANEL_IDS.tree], active: CORE_PANEL_IDS.tree },
    {
      kind: "split",
      id: "s2",
      dir: "col",
      sizes: [0.7, 0.3],
      children: [
        { kind: "viewport" },
        { kind: "group", id: "g2", panels: [CORE_PANEL_IDS.simulation], active: CORE_PANEL_IDS.simulation },
      ],
    },
  ],
};

export function register(): void {
  registerPlugin({
    id: "ui-alt",
    version: "0.2.0",
    coreApiRange: ">=1.13 <2.0",
    schemaVersion: 1,
    uiShells: [
      {
        id: "alt",
        label: "Alt UI",
        description: "Reference alternative viewer UI, composed from core's panels.",
        order: 10,
        layout: {
          grid: GRID,
          open: [CORE_PANEL_IDS.tree, CORE_PANEL_IDS.objectInfo, CORE_PANEL_IDS.scene],
          // Plugin panels that name a dock join these groups as tabs, the first time they are seen.
          docks: { left: "g1", right: "g3" },
          // Each mode keeps its own arrangement; one without a grid starts from the one above.
          modes: [
            { id: "inspect", label: "Inspect", hint: "Browse the model and its properties" },
            {
              id: "results",
              label: "Results",
              hint: "Step through simulation results",
              grid: RESULTS_GRID,
              open: [CORE_PANEL_IDS.tree, CORE_PANEL_IDS.simulation],
              docks: { left: "g1", right: "g2" },
            },
          ],
        },
        // Restyles core's chrome while this shell is active (docs/UI_SHELLS.md lists the tokens).
        themeTokens: { "--ada-tab-active-bg": "#065f46" },
        chrome: {
          // Lazily loaded: the header only downloads when this shell is the active one.
          header: () => import("./AltHeader"),
          toolbar: false,
        },
      },
    ],
  });
}

export default register;
