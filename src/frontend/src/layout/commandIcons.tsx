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
