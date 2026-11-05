// The classic UI's panel components, by panel id, as part of the classic shell's
// own module graph.
//
// Two maps: the classic component of each core panel (content in its classic
// frame -- what the classic preset mounts, pixel-identical to `Menu.tsx`), and
// its frameless content (what another host frames itself).
//
// WHERE THIS MODULE LIVES IN THE BUNDLE matters. Every component here is already
// part of the classic shell's chunk (`app.tsx` and `Menu.tsx` import them
// statically). The catalogue (`./corePanels`) reaches these maps only through a
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
import NodeEditorComponent from "@/components/node_editor/NodeEditorComponent";
import NodeEditorBody from "@/components/node_editor/NodeEditorBody";
import ServerInfoBox, { ServerInfoBody } from "@/components/server_info/ServerInfoBox";
import ObjectInfoBox, { ObjectInfoBody } from "@/components/info_box_selected_object/ObjectInfoBoxComponent";
import SceneInfoBox from "@/components/info_box_scene/SceneInfoBox";
import SceneBody from "@/components/info_box_scene/SceneBody";
import { WebsocketStatusBody, WebsocketStatusBox } from "@/components/WebsocketStatusMenu";
import SimulationControls, { SimulationBody } from "@/components/simulation/SimulationControls";
import SimulationDataInfoPanel from "@/components/simulation/SimulationDataInfoPanel";
import ComponentControls, { ComponentControlsBody } from "@/components/component_view/ComponentControls";
import CellBuilderPanel, { CellBuilderBody } from "@/components/viewer/CellBuilderPanel";

import { CORE_PANEL_IDS } from "./corePanels";
import type { PanelId, PanelProps } from "./panel";

/** A component that ignores the host's `PanelProps`, as a panel body. */
const asBody = (c: React.ComponentType<never>) => c as unknown as React.ComponentType<PanelProps>;

/** The selection tree without its drawer: the Scene | Sources tabs (with a server) over the tree. */
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
  [CORE_PANEL_IDS.nodeEditor]: asBody(NodeEditorComponent),
  [CORE_PANEL_IDS.serverInfo]: asBody(ServerInfoBox),
  [CORE_PANEL_IDS.objectInfo]: asBody(ObjectInfoBox),
  [CORE_PANEL_IDS.scene]: asBody(SceneInfoBox),
  [CORE_PANEL_IDS.wsStatus]: asBody(WebsocketStatusBox),
  [CORE_PANEL_IDS.simulation]: asBody(SimulationControls),
  [CORE_PANEL_IDS.feaTable]: asBody(SimulationDataInfoPanel),
  [CORE_PANEL_IDS.componentControls]: asBody(ComponentControls),
  [CORE_PANEL_IDS.cellbuilder]: asBody(CellBuilderPanel),
};

/** Each core panel's content without its classic frame. */
export const CLASSIC_PANEL_CONTENTS: Readonly<Record<PanelId, React.ComponentType<PanelProps>>> = {
  [CORE_PANEL_IDS.options]: asBody(OptionsBody),
  [CORE_PANEL_IDS.tree]: asBody(SelectionTreeContent),
  [CORE_PANEL_IDS.nodeEditor]: asBody(NodeEditorBody),
  [CORE_PANEL_IDS.serverInfo]: asBody(ServerInfoBody),
  [CORE_PANEL_IDS.objectInfo]: asBody(ObjectInfoBody),
  [CORE_PANEL_IDS.scene]: asBody(SceneBody),
  [CORE_PANEL_IDS.wsStatus]: asBody(WebsocketStatusBody),
  [CORE_PANEL_IDS.simulation]: asBody(SimulationBody),
  [CORE_PANEL_IDS.feaTable]: asBody(SimulationDataInfoPanel),
  [CORE_PANEL_IDS.componentControls]: asBody(ComponentControlsBody),
  [CORE_PANEL_IDS.cellbuilder]: asBody(CellBuilderBody),
};

type Loader = () => Promise<React.ComponentType<PanelProps>>;

/**
 * The panels the classic UI code-splits, loaded from inside this graph with the
 * very specifiers `Menu.tsx`, `app.tsx` and the window hosts use, so they land in
 * the chunks they always had.
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
  [CORE_PANEL_IDS.storage]: () => import("@/components/storage/StorageBrowser").then((m) => asBody(m.StorageBody)),
  [CORE_PANEL_IDS.attributeSearch]: () =>
    import("@/components/asset_browser/AttributeSearchPanel").then((m) => asBody(m.default)),
  [CORE_PANEL_IDS.equipmentAdmin]: () => import("@/components/admin/EquipmentAdminPanel").then((m) => embedded(m.default)),
  [CORE_PANEL_IDS.systemAdmin]: () => import("@/components/admin/SystemAdminPanel").then((m) => embedded(m.default)),
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
