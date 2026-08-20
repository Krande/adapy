import React from "react";

// Name → component registry, plus the <Icon> wrapper every primitive renders through.
//
// Why a registry: the shell is data-driven. A PanelDef, a plugin's topBarButton and a
// command-palette entry all name their icon as a string in a config object; none of
// them can hold a component reference. One map keeps that indirection honest — an
// unknown name is a typed error, not a blank space at runtime.
//
// Every glyph obeys one grammar (viewBox 0 0 24 24, fill none, stroke currentColor,
// width 1.5, round caps/joins) and sets NO colour of its own, so an icon takes the
// colour of whatever it sits in. That is the C4D principle: chrome stays low-chroma
// so the 3D content carries the colour.

import ToggleControlsIcon from "./AnimationControlToggle";
import CellBuilderIcon from "./CellBuilderIcon";
import ChevronRightIcon from "./ChevronRightIcon";
import ComponentIcon from "./ComponentIcon";
import CopyIcon from "./CopyIcon";
import DocumentIcon from "./DocumentIcon";
import EquipmentCatalogIcon from "./EquipmentCatalogIcon";
import ExpandIcon from "./ExpandIcon";
import FEMDataPanelIcon from "./FEMDataPanelIcon";
import FolderClosedIcon from "./FolderClosedIcon";
import FolderOpenIcon from "./FolderOpenIcon";
import GraphIcon from "./GraphIcon";
import GroupIcon from "./GroupIcon";
import InfoIcon from "./InfoIcon";
import PlayPauseIcon from "./PlayPauseIcon";
import PlusIcon from "./PlusIcon";
import PopOutIcon from "./PopOutIcon";
import ProceduralModelIcon from "./ProceduralModelIcon";
import ProcedureIcon from "./ProcedureIcon";
import ReloadIcon from "./ReloadIcon";
import SceneIcon from "./SceneIcon";
import ServerIcon from "./ServerIcon";
import StopIcon from "./StopIcon";
import SystemCatalogIcon from "./SystemCatalogIcon";
import TreeViewIcon from "./TreeViewIcon";
import UploadIcon from "./UploadIcon";
import ViewIcon from "./ViewIcon";
import ViewOffIcon from "./ViewOffIcon";

import {
    CloseIcon,
    DockBottomIcon,
    DockLeftIcon,
    DockRightIcon,
    DownloadIcon,
    FilterIcon,
    FloatIcon,
    MeasureIcon,
    ModeBuildIcon,
    ModeDataIcon,
    ModeConvertIcon,
    ShowAllIcon,
    ModeInspectIcon,
    ModeResultsIcon,
    MoveIcon,
    PinIcon,
    RedoIcon,
    RotateIcon,
    ScaleIcon,
    SearchIcon,
    SectionPlaneIcon,
    SettingsIcon,
    SortIcon,
    ConvertIcon,
    UndoIcon,
} from "./shellIcons";

export const ICONS = {
    // modes
    "mode-inspect": ModeInspectIcon,
    "mode-results": ModeResultsIcon,
    "mode-build": ModeBuildIcon,
    "mode-data": ModeDataIcon,
    "mode-convert": ModeConvertIcon,
    "show-all": ShowAllIcon,
    // panels (existing set)
    tree: TreeViewIcon,
    scene: SceneIcon,
    info: InfoIcon,
    graph: GraphIcon,
    server: ServerIcon,
    component: ComponentIcon,
    cellbuilder: CellBuilderIcon,
    procedural: ProceduralModelIcon,
    procedure: ProcedureIcon,
    "fem-data": FEMDataPanelIcon,
    "equipment-catalog": EquipmentCatalogIcon,
    "system-catalog": SystemCatalogIcon,
    group: GroupIcon,
    document: DocumentIcon,
    "folder-open": FolderOpenIcon,
    "folder-closed": FolderClosedIcon,
    // actions
    play: PlayPauseIcon,
    stop: StopIcon,
    "toggle-controls": ToggleControlsIcon,
    reload: ReloadIcon,
    upload: UploadIcon,
    download: DownloadIcon,
    copy: CopyIcon,
    plus: PlusIcon,
    expand: ExpandIcon,
    chevron: ChevronRightIcon,
    view: ViewIcon,
    "view-off": ViewOffIcon,
    "pop-out": PopOutIcon,
    close: CloseIcon,
    settings: SettingsIcon,
    search: SearchIcon,
    filter: FilterIcon,
    sort: SortIcon,
    convert: ConvertIcon,
    undo: UndoIcon,
    redo: RedoIcon,
    // dock chrome
    pin: PinIcon,
    float: FloatIcon,
    "dock-left": DockLeftIcon,
    "dock-right": DockRightIcon,
    "dock-bottom": DockBottomIcon,
    // viewport tools
    move: MoveIcon,
    rotate: RotateIcon,
    scale: ScaleIcon,
    "section-plane": SectionPlaneIcon,
    measure: MeasureIcon,
} as const;

export type IconName = keyof typeof ICONS;

/** Every registered name — used by the gallery and the registry test. */
export const ICON_NAMES = Object.keys(ICONS) as IconName[];

export type IconSize = "sm" | "md" | "lg" | "xl";

const SIZE_CLASS: Record<IconSize, string> = {
    sm: "w-icon-sm h-icon-sm",
    md: "w-icon-md h-icon-md",
    lg: "w-icon-lg h-icon-lg",
    xl: "w-icon-xl h-icon-xl",
};

export interface IconProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, "children"> {
    name: IconName;
    size?: IconSize;
}

/**
 * Render a registered glyph at a token size.
 *
 * The size is enforced by a wrapper span rather than by passing className to the
 * glyph, because the inherited icon set is not uniform: viewBoxes vary (16, 24 and
 * 36 square) and several components don't spread props onto their <svg> at all, so a
 * className handed to them is silently dropped and the icon renders at its intrinsic
 * size. `[&>svg]:w-full` wins over the SVG's own width/height because CSS beats
 * presentation attributes, so this works regardless of what each glyph does.
 *
 * `aria-hidden` by default: an icon beside a label is decorative, and a standalone
 * icon belongs in an IconButton, which supplies the accessible name.
 */
export function Icon({name, size = "md", className, ...rest}: IconProps) {
    const Glyph = ICONS[name];
    return (
        <span
            aria-hidden="true"
            className={[
                SIZE_CLASS[size],
                "inline-flex items-center justify-center shrink-0",
                "[&>svg]:w-full [&>svg]:h-full [&>svg]:block",
                className,
            ]
                .filter(Boolean)
                .join(" ")}
            {...rest}
        >
            <Glyph focusable="false" />
        </span>
    );
}
