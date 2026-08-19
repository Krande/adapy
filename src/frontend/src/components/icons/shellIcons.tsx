import React from "react";

// Glyphs the new shell needs that the original 29 didn't cover: the four mode marks,
// dock/window controls, list controls, history, and the transform gizmo modes.
//
// Hand-authored rather than pulled from an icon package. Under
// `inlineDynamicImports: true` (the desktop/pip build is one HTML file) tree-shaking
// an icon library is unreliable, and these are ~200 bytes each. Paths adapted from
// Lucide (ISC licence, https://lucide.dev) to match its optical weight.
//
// Grammar, matching the normalised existing set:
//   viewBox 0 0 24 24 · fill none · stroke currentColor · width 1.5 · round caps+joins
// No colour is ever set here — the call site tints via `text-*`.

type P = React.SVGProps<SVGSVGElement>;

const Svg = ({children, ...p}: P & {children: React.ReactNode}) => (
    <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        xmlns="http://www.w3.org/2000/svg"
        {...p}
    >
        {children}
    </svg>
);

/* ---- the four modes ---------------------------------------------------- */

/** Inspect — a magnifier over a face. */
export const ModeInspectIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 7l7-4 7 4v8l-7 4-7-4V7z" />
        <path d="M3 7l7 4 7-4M10 11v8" />
        <circle cx="17.5" cy="17.5" r="3" />
        <path d="M20 20l1.5 1.5" />
    </Svg>
);

/** Results — a field plot with a contour. */
export const ModeResultsIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 20V4M3 20h18" />
        <path d="M6 16c3-6 6 2 9-4 1.2-2.4 2.4-3 3-3" />
        <path d="M6 20v-2M10 20v-5M14 20v-3M18 20v-7" />
    </Svg>
);

/** Build — a cell being placed on a grid. */
export const ModeBuildIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
        <rect x="9" y="9" width="6" height="6" fill="currentColor" fillOpacity="0.25" />
    </Svg>
);

/** Data — stacked stores with a transfer arrow. */
export const ModeDataIcon = (p: P) => (
    <Svg {...p}>
        <ellipse cx="12" cy="6" rx="7" ry="3" />
        <path d="M5 6v6c0 1.66 3.13 3 7 3s7-1.34 7-3V6" />
        <path d="M5 12v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
    </Svg>
);

/* ---- dock / window ----------------------------------------------------- */

export const CloseIcon = (p: P) => (
    <Svg {...p}>
        <path d="M18 6L6 18M6 6l12 12" />
    </Svg>
);

export const PinIcon = (p: P) => (
    <Svg {...p}>
        <path d="M12 17v5" />
        <path d="M9 2h6l-1 6 3 3v2H7v-2l3-3-1-6z" />
    </Svg>
);

export const FloatIcon = (p: P) => (
    <Svg {...p}>
        <rect x="3" y="3" width="13" height="13" rx="1.5" />
        <path d="M8 20h11a2 2 0 0 0 2-2V8" />
    </Svg>
);

export const DockLeftIcon = (p: P) => (
    <Svg {...p}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M9 4v16" />
    </Svg>
);

export const DockRightIcon = (p: P) => (
    <Svg {...p}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M15 4v16" />
    </Svg>
);

export const DockBottomIcon = (p: P) => (
    <Svg {...p}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 14h18" />
    </Svg>
);

/* ---- list controls ----------------------------------------------------- */

export const SearchIcon = (p: P) => (
    <Svg {...p}>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
    </Svg>
);

export const FilterIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 5h18l-7 8v6l-4 2v-8L3 5z" />
    </Svg>
);

export const SortIcon = (p: P) => (
    <Svg {...p}>
        <path d="M7 4v16M7 20l-3-3M7 20l3-3" />
        <path d="M17 20V4M17 4l-3 3M17 4l3 3" />
    </Svg>
);

export const DownloadIcon = (p: P) => (
    <Svg {...p}>
        <path d="M12 3v12M12 15l-4-4M12 15l4-4" />
        <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </Svg>
);

export const SettingsIcon = (p: P) => (
    <Svg {...p}>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6 1.65 1.65 0 0 0 10 3.09V3a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </Svg>
);

/* ---- history ----------------------------------------------------------- */

export const UndoIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 7v6h6" />
        <path d="M3 13a9 9 0 1 0 3-6.7L3 9" />
    </Svg>
);

export const RedoIcon = (p: P) => (
    <Svg {...p}>
        <path d="M21 7v6h-6" />
        <path d="M21 13a9 9 0 1 1-3-6.7L21 9" />
    </Svg>
);

/* ---- transform gizmo --------------------------------------------------- */

export const MoveIcon = (p: P) => (
    <Svg {...p}>
        <path d="M12 2v20M2 12h20" />
        <path d="M12 2l-3 3M12 2l3 3M12 22l-3-3M12 22l3-3M2 12l3-3M2 12l3 3M22 12l-3-3M22 12l-3 3" />
    </Svg>
);

export const RotateIcon = (p: P) => (
    <Svg {...p}>
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <path d="M21 3v6h-6" />
    </Svg>
);

export const ScaleIcon = (p: P) => (
    <Svg {...p}>
        <path d="M4 20L20 4" />
        <path d="M14 4h6v6" />
        <rect x="3" y="15" width="6" height="6" rx="1" />
    </Svg>
);

/** Section plane — a cut through a solid. */
export const SectionPlaneIcon = (p: P) => (
    <Svg {...p}>
        <path d="M4 8l8-4 8 4v8l-8 4-8-4V8z" />
        <path d="M2 14l20-6" strokeDasharray="3 2" />
    </Svg>
);

/** Measure — a dimension line with end ticks. */
export const MeasureIcon = (p: P) => (
    <Svg {...p}>
        <path d="M3 12h18" />
        <path d="M3 8v8M21 8v8" />
        <path d="M9 10v4M15 10v4" />
    </Svg>
);
