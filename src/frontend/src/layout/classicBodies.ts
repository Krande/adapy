// The classic UI's panel components, gathered behind ONE lazy import for the
// core panel catalogue (`./corePanels`).
//
// One module rather than one dynamic import per component: every component here
// is already part of the classic shell's chunk, and each separate dynamic import
// of one would make the bundler split that component (and what it shares) out
// into a chunk of its own. Through this module the hosted build gains one shared
// chunk instead of dozens, and the desktop single-file build is unaffected.

export { default as OptionsComponent } from "@/components/OptionsComponent";
export { default as ResizableTreeView } from "@/components/tree_view/ResizableTreeView";
export { default as NodeEditorComponent } from "@/components/node_editor/NodeEditorComponent";
export { default as ServerInfoBox } from "@/components/server_info/ServerInfoBox";
export { default as ObjectInfoBox } from "@/components/info_box_selected_object/ObjectInfoBoxComponent";
export { default as SceneInfoBox } from "@/components/info_box_scene/SceneInfoBox";
export { WebsocketStatusBox } from "@/components/WebsocketStatusMenu";
export { default as SimulationControls } from "@/components/simulation/SimulationControls";
export { default as SimulationDataInfoPanel } from "@/components/simulation/SimulationDataInfoPanel";
export { default as ComponentControls } from "@/components/component_view/ComponentControls";
export { default as CellBuilderPanel } from "@/components/viewer/CellBuilderPanel";
