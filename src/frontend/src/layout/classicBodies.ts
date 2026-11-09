// The classic UI's panel components, by panel id, as part of the classic shell's
// own module graph.
//
// Two maps: the classic component of each core panel (content in its classic
// frame -- what the classic preset mounts), and its frameless content (what
// another host frames itself).
//
// WHERE THIS MODULE LIVES IN THE BUNDLE matters. Every component here is part of
// the classic shell's chunk (`app.tsx` re-exports this module statically). The catalogue (`./corePanels`) reaches these maps only through a
// dynamic import of the shell module itself (`import("@/app")`), which re-exports
// them, and the layout host imports them statically from inside that graph. A
// dynamic import of THIS module from outside would make it a chunk entry of its
// own and pull every component it names (and what they share) out of the shell's
// chunk into a new one -- which is what an earlier revision did.
//
// Panels whose component is already a lazy chunk of its own (storage, attribute
// search, admin, convert, the catalog editors) are not here: the catalogue loads
// those chunks directly, as the classic UI always has.

import React from "react";

import OptionsComponent, { OptionsBody } from "@/components/OptionsComponent";
import ResizableTreeView from "@/components/tree_view/ResizableTreeView";
import AssetBrowser, { AssetBrowserTabs, assetsTabAvailable } from "@/components/asset_browser/AssetBrowser";
import ServerInfoBox, { ServerInfoBody } from "@/components/server_info/ServerInfoBox";
import ObjectInfoBox, { ObjectInfoBody } from "@/components/info_box_selected_object/ObjectInfoBoxComponent";
import SceneInfoBox from "@/components/info_box_scene/SceneInfoBox";
import SceneBody from "@/components/info_box_scene/SceneBody";
import { WebsocketStatusBody, WebsocketStatusBox } from "@/components/WebsocketStatusMenu";
import SimulationControls, { SimulationBody } from "@/components/simulation/SimulationControls";
import SimulationDataInfoPanel from "@/components/simulation/SimulationDataInfoPanel";
import ComponentControls, { ComponentControlsBody } from "@/components/component_view/ComponentControls";
import CellBuilderPanel, { CellBuilderBody } from "@/components/viewer/CellBuilderPanel";
import { ComponentListBody, ComponentListPanel } from "@/components/viewer/cellbuilder/panel/ComponentList";

import { CORE_PANEL_IDS } from "./corePanels";
import type { PanelId, PanelProps } from "./panel";

/** A component that ignores the host's `PanelProps`, as a panel body. */
const asBody = (c: React.ComponentType<never>) => c as unknown as React.ComponentType<PanelProps>;

/** A frameless content component, given a shell's options (`PanelProps.options`) as its props. */
const withOptions = <P extends object>(c: React.ComponentType<P>, base?: Partial<P>): React.ComponentType<PanelProps> => {
  const C: React.FC<PanelProps> = (p) => React.createElement(c, { ...base, ...p.options } as P);
  C.displayName = `Options(${c.displayName ?? c.name ?? "content"})`;
  return C;
};

/**
 * The selection tree without its drawer: the Scene | Systems | Sources tabs (with a server)
 * over the tree. In a pop-out window the Scene tree shows the main window's tree
 * (`core:scene-tree`) and the Systems tab acts through it; Sources, which loads models into
 * the scene, is not offered there.
 */
const SelectionTreeContent: React.FC = () =>
  React.createElement(
    "div",
    { className: "flex flex-col h-full min-h-0 text-white" },
    assetsTabAvailable()
      ? React.createElement("div", { className: "flex items-center h-9 shrink-0 border-b border-gray-700/80" }, React.createElement(AssetBrowserTabs))
      : null,
    React.createElement(AssetBrowser),
  );
/** A catalog editor as the admin panel embeds it: no floating card of its own. */
const embedded = (c: React.ComponentType<{ embedded?: boolean }>) =>
  asBody(() => React.createElement(c, { embedded: true }));

/** Each core panel's classic component: its content in its classic frame. */
export const CLASSIC_PANEL_COMPONENTS: Readonly<Record<PanelId, React.ComponentType<PanelProps>>> = {
  [CORE_PANEL_IDS.options]: asBody(OptionsComponent),
  [CORE_PANEL_IDS.tree]: asBody(ResizableTreeView),
  [CORE_PANEL_IDS.serverInfo]: asBody(ServerInfoBox),
  [CORE_PANEL_IDS.objectInfo]: asBody(ObjectInfoBox),
  [CORE_PANEL_IDS.scene]: asBody(SceneInfoBox),
  [CORE_PANEL_IDS.wsStatus]: asBody(WebsocketStatusBox),
  [CORE_PANEL_IDS.simulation]: asBody(SimulationControls),
  [CORE_PANEL_IDS.feaTable]: asBody(SimulationDataInfoPanel),
  [CORE_PANEL_IDS.componentControls]: asBody(ComponentControls),
  [CORE_PANEL_IDS.cellbuilder]: asBody(CellBuilderPanel),
  [CORE_PANEL_IDS.componentList]: asBody(ComponentListPanel),
};

/** Each core panel's content without its classic frame. */
export const CLASSIC_PANEL_CONTENTS: Readonly<Record<PanelId, React.ComponentType<PanelProps>>> = {
  [CORE_PANEL_IDS.options]: withOptions(OptionsBody),
  [CORE_PANEL_IDS.tree]: asBody(SelectionTreeContent),
  [CORE_PANEL_IDS.serverInfo]: withOptions(ServerInfoBody),
  [CORE_PANEL_IDS.objectInfo]: withOptions(ObjectInfoBody),
  [CORE_PANEL_IDS.scene]: withOptions(SceneBody),
  [CORE_PANEL_IDS.wsStatus]: withOptions(WebsocketStatusBody),
  [CORE_PANEL_IDS.simulation]: withOptions(SimulationBody),
  [CORE_PANEL_IDS.feaTable]: withOptions(SimulationDataInfoPanel),
  [CORE_PANEL_IDS.componentControls]: withOptions(ComponentControlsBody),
  [CORE_PANEL_IDS.cellbuilder]: withOptions(CellBuilderBody),
  [CORE_PANEL_IDS.componentList]: asBody(ComponentListBody),
};

type Loader = () => Promise<React.ComponentType<PanelProps>>;

/**
 * The panels the classic UI code-splits, loaded from inside this graph with the
 * specifiers they have always had (`app.tsx`'s canvas-less pages use the same),
 * so they land in the chunks they always had.
 */
const LAZY_BODIES: Readonly<Record<PanelId, Loader>> = {
  [CORE_PANEL_IDS.storage]: () => import("@/components/storage/StorageBrowser").then((m) => asBody(m.default)),
  [CORE_PANEL_IDS.attributeSearch]: () =>
    import("@/components/asset_browser/AttributeSearchWindow").then((m) => asBody(m.default)),
  [CORE_PANEL_IDS.equipmentAdmin]: () => import("@/components/admin/EquipmentAdminPanel").then((m) => asBody(m.default)),
  [CORE_PANEL_IDS.systemAdmin]: () => import("@/components/admin/SystemAdminPanel").then((m) => asBody(m.default)),
  // Embedded, on the tab the viewer-panel store asks for, as the in-viewer window shows it.
  [CORE_PANEL_IDS.admin]: () => import("@/components/admin/AdminPanel").then((m) => asBody(m.AdminWindowBody)),
  [CORE_PANEL_IDS.convert]: () => import("@/components/convert/ConvertPage").then((m) => asBody(m.default)),
};

const LAZY_CONTENTS: Readonly<Record<PanelId, Loader>> = {
  // In a group or window the layout sizes the panel: the file list takes that height.
  [CORE_PANEL_IDS.storage]: () =>
    import("@/components/storage/StorageBrowser").then((m) => withOptions(m.StorageBody, { fillHeight: true })),
  [CORE_PANEL_IDS.attributeSearch]: () =>
    import("@/components/asset_browser/AttributeSearchPanel").then((m) => withOptions(m.default)),
  [CORE_PANEL_IDS.equipmentAdmin]: () => import("@/components/admin/EquipmentAdminPanel").then((m) => embedded(m.default)),
  [CORE_PANEL_IDS.systemAdmin]: () => import("@/components/admin/SystemAdminPanel").then((m) => embedded(m.default)),
  // The same components as their bodies, given a shell's options (`ConvertBodyProps.inViewer`, ...).
  [CORE_PANEL_IDS.admin]: () => import("@/components/admin/AdminPanel").then((m) => withOptions(m.AdminWindowBody)),
  [CORE_PANEL_IDS.convert]: () => import("@/components/convert/ConvertPage").then((m) => withOptions(m.default)),
};

/**
 * A core panel's classic component (`"body"`) or frameless content
 * (`"content"`), React.lazy-compatible. A panel without separate content (it is
 * frameless already, or not split) answers `"content"` with its body.
 */
export async function loadClassicPanel(
  id: PanelId,
  which: "body" | "content",
): Promise<{ default: React.ComponentType<PanelProps> }> {
  const sync = which === "content" ? (CLASSIC_PANEL_CONTENTS[id] ?? CLASSIC_PANEL_COMPONENTS[id]) : CLASSIC_PANEL_COMPONENTS[id];
  if (sync) return { default: sync };
  const lazy = (which === "content" ? LAZY_CONTENTS[id] : undefined) ?? LAZY_BODIES[id];
  if (!lazy) throw new Error(`[layout] no classic component for panel "${id}"`);
  return { default: await lazy() };
}
