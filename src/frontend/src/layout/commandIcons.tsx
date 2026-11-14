// Icons for core's commands (`CommandSpec.icon`): what the tool rail draws on a
// button, and what the marking menu and the rail's customise dialog show beside
// a label. Plain 24-unit stroke glyphs in `currentColor`, so they take the
// chrome's text colour in every theme. A command without an icon is drawn with
// its initials (`CommandGlyph`).

import React from "react";

import { CORE_COMMAND_IDS as C, cameraViewCommandId } from "./coreCommandIds";

const glyph = (body: React.ReactNode, name: string): React.FC => {
  const Icon: React.FC = () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {body}
    </svg>
  );
  Icon.displayName = `${name}Icon`;
  return Icon;
};

const eye = <path d="M2.5 12C4.5 7.5 8 5.5 12 5.5s7.5 2 9.5 6.5c-2 4.5-5.5 6.5-9.5 6.5S4.5 16.5 2.5 12Z" />;
const pupil = <circle cx="12" cy="12" r="2.8" />;
const cube = (
  <>
    <path d="M12 3 20 7.5v9L12 21l-8-4.5v-9Z" />
    <path d="M4 7.5 12 12l8-4.5M12 12v9" />
  </>
);

export const OpenIcon = glyph(<path d="M3 7.5V18a1.5 1.5 0 0 0 1.5 1.5h15A1.5 1.5 0 0 0 21 18v-8a1.5 1.5 0 0 0-1.5-1.5H12L10 6H4.5A1.5 1.5 0 0 0 3 7.5Z" />, "Open");
export const UploadIcon = glyph(<path d="M12 16V4m-4.5 4.5L12 4l4.5 4.5M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" />, "Upload");
export const ExportIcon = glyph(<path d="M12 4v12m-4.5-4.5L12 16l4.5-4.5M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" />, "Export");
export const UndoIcon = glyph(<path d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />, "Undo");
export const RedoIcon = glyph(<path d="m15 14 5-5-5-5m5 5H9.5a5.5 5.5 0 0 0 0 11H13" />, "Redo");
export const SelectAllIcon = glyph(
  <>
    <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </>,
  "SelectAll",
);
export const InvertSelectionIcon = glyph(
  <>
    <rect x="4" y="4" width="16" height="16" rx="1.5" />
    <path d="M4 20 20 4" />
    <path d="M7 4v0M4 7l3-3M4 11l7-7M4 15 15 4" strokeWidth="1" />
  </>,
  "InvertSelection",
);
export const ClearSelectionIcon = glyph(
  <>
    <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
    <path d="m9 9 6 6m0-6-6 6" />
  </>,
  "ClearSelection",
);
export const CopyIcon = glyph(
  <>
    <rect x="8" y="8" width="12" height="12" rx="1.5" />
    <path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" />
  </>,
  "Copy",
);
export const GroupIcon = glyph(<path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2M16 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2M9 12h6" />, "Group");
export const HideIcon = glyph(
  <>
    {eye}
    <path d="M4 4l16 16" />
  </>,
  "Hide",
);
export const HideOthersIcon = glyph(
  <>
    {eye}
    {pupil}
    <path d="M3 3h4M3 3v4M21 21h-4M21 21v-4" />
  </>,
  "HideOthers",
);
export const ShowAllIcon = glyph(
  <>
    {eye}
    {pupil}
  </>,
  "ShowAll",
);
export const FitAllIcon = glyph(<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5M9 9l-5-5M15 9l5-5M15 15l5 5M9 15l-5 5" />, "FitAll");
export const FitSelectionIcon = glyph(
  <>
    <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" />
    <rect x="9" y="9" width="6" height="6" rx="1" />
  </>,
  "FitSelection",
);
export const HomeIcon = glyph(<path d="M4 11 12 4l8 7M6.5 9.5V20h11V9.5M10 20v-5h4v5" />, "Home");
export const ViewIcon = glyph(cube, "View");
export const TopViewIcon = glyph(
  <>
    {cube}
    <path d="M12 3 20 7.5 12 12 4 7.5Z" fill="currentColor" fillOpacity="0.35" />
  </>,
  "TopView",
);
export const FrontViewIcon = glyph(
  <>
    {cube}
    <path d="M4 7.5 12 12v9l-8-4.5Z" fill="currentColor" fillOpacity="0.35" />
  </>,
  "FrontView",
);
export const SideViewIcon = glyph(
  <>
    {cube}
    <path d="M20 7.5 12 12v9l8-4.5Z" fill="currentColor" fillOpacity="0.35" />
  </>,
  "SideView",
);
export const EdgesIcon = glyph(
  <>
    {cube}
    <path d="M4 16.5 12 12l8 4.5" strokeDasharray="2 2" strokeWidth="1.2" />
  </>,
  "Edges",
);
/** Orthographic view: a box drawn with parallel edges (no vanishing point). */
export const OrthographicIcon = glyph(
  <>
    <rect x="4" y="8" width="12" height="12" />
    <path d="M4 8l4-4h12v12l-4 4M16 8l4-4M16 8v0" />
  </>,
  "Orthographic",
);
/** Ground grid: a grid laid flat. */
export const GroundGridIcon = glyph(<path d="M3 15l5-6h13l-5 6Z M7.2 10.2h13 M4.7 13.2h13 M9.5 9l-4 6 M13.5 9l-4 6 M17.5 9l-4 6" />, "GroundGrid");
export const StatsIcon = glyph(<path d="M4 20V10M10 20V4M16 20v-7M21 20H3" />, "Stats");
export const GalleryIcon = glyph(
  <>
    <rect x="4" y="4" width="7" height="7" rx="1" />
    <rect x="13" y="4" width="7" height="7" rx="1" />
    <rect x="4" y="13" width="7" height="7" rx="1" />
    <rect x="13" y="13" width="7" height="7" rx="1" />
  </>,
  "Gallery",
);
export const FullscreenIcon = glyph(<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />, "Fullscreen");
export const PaletteIcon = glyph(
  <>
    <circle cx="10.5" cy="10.5" r="6" />
    <path d="m15 15 5 5" />
  </>,
  "Palette",
);
export const IsolateIcon = glyph(<path d="M4 5h16l-6 7.5V19l-4 1.5v-8Z" />, "Isolate");
export const ClearIsolationIcon = glyph(
  <>
    <path d="M4 5h16l-6 7.5V19l-4 1.5v-8Z" />
    <path d="m16 15 5 5m0-5-5 5" />
  </>,
  "ClearIsolation",
);
export const WireframeIcon = glyph(<path d="M4 4h16v16H4ZM4 12h16M12 4v16M4 4l16 16" />, "Wireframe");
export const NewWindowIcon = glyph(
  <>
    <rect x="3" y="6" width="13" height="13" rx="1.5" />
    <path d="M8 3h11.5A1.5 1.5 0 0 1 21 4.5V16" />
  </>,
  "NewWindow",
);
export const ResetLayoutIcon = glyph(
  <>
    <rect x="3.5" y="4" width="17" height="16" rx="1.5" />
    <path d="M9 4v16M9 12h11.5" />
  </>,
  "ResetLayout",
);
export const KeyboardIcon = glyph(
  <>
    <rect x="2.5" y="6" width="19" height="12" rx="1.5" />
    <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7.5 14h9" />
  </>,
  "Keyboard",
);
export const InfoIcon = glyph(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5.5M12 7.5h.01" />
  </>,
  "Info",
);
export const MarkingMenuIcon = glyph(
  <>
    <circle cx="12" cy="12" r="2" />
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
  </>,
  "MarkingMenu",
);
export const CustomiseIcon = glyph(<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M16 4v4M10 10v4M18 16v4" />, "Customise");

export const PlayIcon = glyph(<path d="M7 4.5v15l12-7.5Z" />, "Play");
export const PauseIcon = glyph(<path d="M8 5v14M16 5v14" />, "Pause");
export const StopResultsIcon = glyph(<rect x="6" y="6" width="12" height="12" rx="1" />, "StopResults");
export const StepBackIcon = glyph(<path d="M7 5v14M19 5.5v13L10 12Z" />, "StepBack");
export const StepForwardIcon = glyph(<path d="M17 5v14M5 5.5v13L14 12Z" />, "StepForward");
export const SectionIcon = glyph(
  <>
    <path d="M12 3 20 7.5v9L12 21l-8-4.5v-9Z" />
    <path d="M3 13.5 12 9l9 4.5-9 4.5Z" strokeWidth="1.4" />
  </>,
  "Section",
);
export const FlipIcon = glyph(<path d="M4 8h14l-3-3M20 16H6l3 3" />, "Flip");
export const UndeformedIcon = glyph(
  <>
    <path d="M4 17c3-9 13-9 16 0" />
    <path d="M4 17h16" strokeDasharray="2 2" />
  </>,
  "Undeformed",
);
export const ElementEdgesIcon = glyph(<path d="M4 4h16v16H4ZM4 12h16M12 4v16" />, "ElementEdges");
export const ResultColorsIcon = glyph(
  <>
    <rect x="4" y="4" width="16" height="16" rx="1.5" />
    <path d="M4 9.5h16M4 14.5h16" />
  </>,
  "ResultColors",
);
export const NodeLabelsIcon = glyph(
  <>
    <circle cx="6" cy="6" r="2" />
    <path d="M11 6h9M11 10h5" />
    <circle cx="6" cy="16" r="2" />
    <path d="M11 16h9M11 20h5" />
  </>,
  "NodeLabels",
);
export const ElementLabelsIcon = glyph(
  <>
    <rect x="3.5" y="4" width="17" height="16" rx="1.5" />
    <path d="M8 10h8M8 14h5" />
  </>,
  "ElementLabels",
);
export const ColorByIcon = glyph(
  <>
    <circle cx="8" cy="9" r="3.5" />
    <circle cx="16" cy="9" r="3.5" strokeDasharray="2 2" />
    <circle cx="12" cy="16" r="3.5" />
  </>,
  "ColorBy",
);
export const CompileIcon = glyph(
  <>
    <path d="M8 5 3 12l5 7M16 5l5 7-5 7" />
    <path d="m13 4-2 16" />
  </>,
  "Compile",
);
export const CommitIcon = glyph(
  <>
    <path d="M5 4h11l3 3v13H5Z" />
    <path d="M8 4v5h7V4M8 20v-6h8v6" />
  </>,
  "Commit",
);
export const CloseModelIcon = glyph(<path d="m6 6 12 12M18 6 6 18" />, "CloseModel");
export const LockIcon = glyph(
  <>
    <rect x="5" y="10.5" width="14" height="9.5" rx="1.5" />
    <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
  </>,
  "Lock",
);
export const ParentIcon = glyph(<path d="M12 19V5m-5.5 5.5L12 5l5.5 5.5M5 21h14" />, "Parent");
export const ChildIcon = glyph(<path d="M12 5v14m-5.5-5.5L12 19l5.5-5.5M5 3h14" />, "Child");
export const PrevSiblingIcon = glyph(<path d="M19 12H5m5.5-5.5L5 12l5.5 5.5M21 5v14" />, "PrevSibling");
export const NextSiblingIcon = glyph(<path d="M5 12h14m-5.5-5.5L19 12l-5.5 5.5M3 5v14" />, "NextSibling");
export const LegendIcon = glyph(
  <>
    <rect x="5" y="3.5" width="5" height="17" rx="1" />
    <path d="M13.5 6.5h6M13.5 12h6M13.5 17.5h6" />
  </>,
  "Legend",
);
export const FemConceptsIcon = glyph(
  <>
    <path d="M4 20h16M7 20v-5m10 5v-5" />
    <path d="M5 15h14M12 4v8m-3-3 3 3 3-3" />
  </>,
  "FemConcepts",
);
export const DataDockIcon = glyph(
  <>
    <rect x="3.5" y="4" width="17" height="16" rx="1.5" />
    <path d="M3.5 9h17M3.5 14h17M9.5 9v11" />
  </>,
  "DataDock",
);
export const BeamSectionsIcon = glyph(
  <>
    <path d="M3 8h18M3 16h18" />
    <path d="M9 8v8m6-8v8" strokeWidth="1.2" />
  </>,
  "BeamSections",
);
export const WarpIcon = glyph(
  <>
    <path d="M3 12h18" strokeDasharray="2 2" />
    <path d="M3 12c4-9 6 9 9 0s5-9 9 0" />
  </>,
  "Warp",
);
export const ValueLabelsIcon = glyph(
  <>
    <rect x="3.5" y="4" width="17" height="16" rx="1.5" />
    <path d="M8 15 11 9l2 4 2-3" />
  </>,
  "ValueLabels",
);
export const AddCellIcon = glyph(
  <>
    <path d="M12 3 20 7.5v9L12 21l-8-4.5v-9Z" />
    <path d="M12 9v6M9 12h6" />
  </>,
  "AddCell",
);
export const AddLoftIcon = glyph(
  <>
    <ellipse cx="12" cy="6" rx="5" ry="2" />
    <ellipse cx="12" cy="18" rx="7" ry="2.5" />
    <path d="M7 6l-5 12M17 6l5 12" strokeWidth="1.2" />
  </>,
  "AddLoft",
);
export const AddOpeningIcon = glyph(
  <>
    <rect x="3.5" y="3.5" width="17" height="17" rx="1" />
    <rect x="9" y="9" width="6" height="11.5" />
  </>,
  "AddOpening",
);
export const AddEquipmentIcon = glyph(
  <>
    <rect x="5" y="9" width="14" height="10" rx="1.5" />
    <path d="M9 9V6h6v3M12 13v3" />
  </>,
  "AddEquipment",
);
export const TranslateIcon = glyph(<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3" />, "Translate");
export const RotateIcon = glyph(<path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5" />, "Rotate");
export const ResizeIcon = glyph(<path d="M5 19 19 5M13 5h6v6M11 19H5v-6" />, "Resize");
export const TopologyIcon = glyph(<path d="M4 4h16v16H4ZM4 12h16M12 4v16" strokeDasharray="3 2" />, "Topology");
export const SimulationViewIcon = glyph(<path d="M4 18 9 7l5 8 3-5 3 8M4 20h16" />, "SimulationView");
export const DetailViewIcon = glyph(
  <>
    <circle cx="10.5" cy="10.5" r="6" />
    <path d="m15 15 5 5M10.5 8v5M8 10.5h5" />
  </>,
  "DetailView",
);
export const OverlayIcon = glyph(
  <>
    <rect x="3.5" y="3.5" width="12" height="12" rx="1" />
    <rect x="8.5" y="8.5" width="12" height="12" rx="1" />
  </>,
  "Overlay",
);
export const SideBySideIcon = glyph(
  <>
    <rect x="3" y="6" width="8" height="12" rx="1" />
    <rect x="13" y="6" width="8" height="12" rx="1" />
  </>,
  "SideBySide",
);

/** Core's commands' icons, by command id. */
export const CORE_COMMAND_ICONS: Readonly<Record<string, React.FC>> = {
  [C.openFile]: OpenIcon,
  [C.uploadFile]: UploadIcon,
  [C.exportSelection]: ExportIcon,
  [C.exportModel]: ExportIcon,
  [C.undo]: UndoIcon,
  [C.redo]: RedoIcon,
  [C.selectAll]: SelectAllIcon,
  [C.invertSelection]: InvertSelectionIcon,
  [C.clearSelection]: ClearSelectionIcon,
  [C.copyNames]: CopyIcon,
  [C.groupSelection]: GroupIcon,
  [C.hideSelected]: HideIcon,
  [C.hideOthers]: HideOthersIcon,
  [C.showAll]: ShowAllIcon,
  [C.fitAll]: FitAllIcon,
  [C.fitSelection]: FitSelectionIcon,
  [C.resetCamera]: HomeIcon,
  [cameraViewCommandId("top")]: TopViewIcon,
  [cameraViewCommandId("bottom")]: TopViewIcon,
  [cameraViewCommandId("front")]: FrontViewIcon,
  [cameraViewCommandId("back")]: FrontViewIcon,
  [cameraViewCommandId("left")]: SideViewIcon,
  [cameraViewCommandId("right")]: SideViewIcon,
  [cameraViewCommandId("isometric")]: ViewIcon,
  [C.edges]: EdgesIcon,
  [C.orthographic]: OrthographicIcon,
  [C.groundGrid]: GroundGridIcon,
  [C.stats]: StatsIcon,
  [C.gallery]: GalleryIcon,
  [C.fullscreen]: FullscreenIcon,
  [C.commandPalette]: PaletteIcon,
  [C.feaIsolateSets]: IsolateIcon,
  [C.feaIsolateSelectionSets]: IsolateIcon,
  [C.feaClearIsolation]: ClearIsolationIcon,
  [C.feaToggleContextWireframe]: WireframeIcon,
  [C.newWindow]: NewWindowIcon,
  [C.resetLayout]: ResetLayoutIcon,
  [C.resetAllLayouts]: ResetLayoutIcon,
  [C.shortcuts]: KeyboardIcon,
  [C.about]: InfoIcon,
  [C.markingMenu]: MarkingMenuIcon,
  [C.toggleMarkingMenu]: MarkingMenuIcon,
  [C.customiseRail]: CustomiseIcon,
  [C.playPause]: PlayIcon,
  [C.stopResults]: StopResultsIcon,
  [C.stepBack]: StepBackIcon,
  [C.stepForward]: StepForwardIcon,
  [C.sectionX]: SectionIcon,
  [C.sectionY]: SectionIcon,
  [C.sectionZ]: SectionIcon,
  [C.sectionToggle]: SectionIcon,
  [C.sectionFlip]: FlipIcon,
  [C.sectionClear]: ClearSelectionIcon,
  [C.feaToggleUndeformed]: UndeformedIcon,
  [C.feaToggleElementEdges]: ElementEdgesIcon,
  [C.feaToggleResultColors]: ResultColorsIcon,
  [C.labelsNodes]: NodeLabelsIcon,
  [C.labelsElements]: ElementLabelsIcon,
  [C.colorByNext]: ColorByIcon,
  [C.colorByModel]: ColorByIcon,
  [C.colorByReset]: ClearSelectionIcon,
  [C.builderUndo]: UndoIcon,
  [C.builderRedo]: RedoIcon,
  [C.builderCompile]: CompileIcon,
  [C.builderRecompile]: CompileIcon,
  [C.builderCompileBrowser]: CompileIcon,
  [C.builderCommit]: CommitIcon,
  [C.builderClose]: CloseModelIcon,
  [C.builderNew]: AddCellIcon,
  [C.toggleUiFreeze]: LockIcon,
  [C.selectParent]: ParentIcon,
  [C.selectChild]: ChildIcon,
  [C.selectPrevSibling]: PrevSiblingIcon,
  [C.selectNextSibling]: NextSiblingIcon,
  [C.colorLegend]: LegendIcon,
  [C.femConcepts]: FemConceptsIcon,
  [C.dataDock]: DataDockIcon,
  [C.beamSections]: BeamSectionsIcon,
  [C.warp]: WarpIcon,
  [C.labelsValues]: ValueLabelsIcon,
  [C.builderAddCell]: AddCellIcon,
  [C.builderAddLoft]: AddLoftIcon,
  [C.builderAddOpening]: AddOpeningIcon,
  [C.builderAddEquipment]: AddEquipmentIcon,
  [C.builderTranslate]: TranslateIcon,
  [C.builderRotate]: RotateIcon,
  [C.builderResize]: ResizeIcon,
  [C.builderRepTopology]: TopologyIcon,
  [C.builderRepSimulation]: SimulationViewIcon,
  [C.builderRepDetail]: DetailViewIcon,
  [C.builderSuperimpose]: OverlayIcon,
  [C.builderSideBySide]: SideBySideIcon,
};


/** A command's initials, for one without an icon ("Fit selection" -> "FS"). */
export function commandInitials(label: string): string {
  const words = label.replace(/[^\p{L}\p{N} ]+/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
}

/** A command's icon, or its initials in the icon's place. */
export function CommandGlyph({ icon: Icon, label }: { icon?: React.FC; label: string }) {
  if (Icon) return <Icon />;
  return (
    <span aria-hidden="true" className="inline-flex h-4 min-w-4 items-center justify-center text-[10px] font-semibold leading-none">
      {commandInitials(label)}
    </span>
  );
}
