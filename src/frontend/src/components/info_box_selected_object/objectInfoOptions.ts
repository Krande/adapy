// `ObjectInfoBody`'s own options type and the context that threads it down to its sections
// (`ObjectMetadata`, `FeaSets`, `FlatFeaElement`, `SelectionSummary`/`SummaryMenu`), split out of
// ObjectInfoBoxComponent.tsx so a core section file can read the context without importing the
// whole component module.

import React from "react";
import type { SelectionSnapshot } from "@/plugins/propertyProviders";
import type { PropertyProviderLayout } from "@/plugins/propertyProviders";

/** Options of the Selected Object panel's content. Absent, each leaves the panel as it is. */
export interface ObjectInfoBodyProps {
  /** Replaces what "Show in data" does (open the FEA data table at the node), given the
   *  picked element's first node. */
  onShowInData?: (nodeId: number) => void;
  /**
   * Which of the matching sections to draw, by provider id (core's and the plugins'), asked
   * after their own `match`. A shell sets it per mode (`PanelOverride.options` as a function
   * of the mode) so, say, the builder's sections stay in the mode that edits the model.
   * A throw keeps the section. Absent: every matching section. Runs AFTER `providers` below,
   * so passing both is safe.
   */
  sections?: (providerId: string, sel: SelectionSnapshot) => boolean;
  /**
   * A static show/hide/order over the matching providers (`applyProviderLayout`), set by the
   * active shell (`PanelOverride.options(mode)` for `core:object-info`). Unlike `sections`, it
   * cannot see the selection -- a shell that needs a selection-dependent rule keeps using
   * `sections` for that, alongside this for the parts that don't need one (e.g. a fixed order).
   */
  providers?: PropertyProviderLayout;
  /**
   * The picked element's properties (`object-metadata`) as plain rows: no foldable
   * "Properties" heading, always open, and its clicked-at decimals in that row's own menu.
   * For a shell whose panel is itself the properties. Absent: folded under a heading, as before.
   */
  flat?: boolean;
  /**
   * False: with a builder item picked, the summary at the top (its name again, Hide, Unhide
   * all, Add mode) is left out -- the item's own layout names it, and a shell's Model panel
   * and toolbar act on it. A mesh pick keeps its summary. Absent or true: drawn, as before.
   */
  builderSummary?: boolean;
  /**
   * The summary at the top of the panel. "full" (the default): as it always was -- the name row,
   * Hide / Hide others / Unhide all, Show in data, Download as…, its notices, and in the object
   * metadata the "Clicked at" row and the "Mesh (n tris)" section. "menu": none of those; one
   * small "⋯" button at the top right of the body instead, whose menu holds Show in data,
   * Download as… (greyed out, the reason its tooltip, when there is no source), Copy clicked
   * position, and the mesh's stats as a line. For a flat panel whose items name what is picked
   * themselves, with the tree and the keys to hide it.
   */
  summary?: "full" | "menu";
  /**
   * Which pick this panel shows: "builder" shows only a builder/ref pick, falling through to
   * `kind: "none"` (plus the active structure's section) when there is none -- a scene (GLB)
   * pick never shows up here, however stale a pick lingering in the scene's own stores might
   * be. "scene" is the mirror: only a scene pick, a builder pick never shown. Absent or "any"
   * (the default): today's behaviour, whichever exists wins, cell/ref first. Each mode keeps
   * its own kind of pick (the stores already do; this only changes what the snapshot — and so
   * every section below — is told to look at), so a GLB pick made while comparing a result in
   * Output does not leak into Input's panel, and the reverse.
   */
  selectionSource?: "builder" | "scene" | "any";
}

export const ObjectInfoOptions = React.createContext<ObjectInfoBodyProps>({});
