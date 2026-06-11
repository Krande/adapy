import React from "react";

// Small file-type glyph for storage rows: a document outline with a
// colour-coded fold, keyed on extension family. One SVG shape, colour
// carries the meaning — geometry (CAD sources), FEA models, FEA
// results, GLB view artefacts, profiles, fallback grey.
//
// Colours are deliberately muted so a long list doesn't turn into a
// fruit salad; the loaded-state blue tint on the filename stays the
// strongest signal in the row.

type Family = "geometry" | "fea-model" | "fea-result" | "view" | "profile" | "other";

function familyOf(name: string): Family {
    const lower = name.toLowerCase();
    const ext = lower.slice(lower.lastIndexOf(".") + 1);
    switch (ext) {
        case "ifc":
        case "step":
        case "stp":
        case "sat":
            return "geometry";
        case "inp":
        case "fem":
        case "sif":
            return "fea-model";
        case "sin":
        case "rmed":
        case "med":
        case "frd":
        case "odb":
            return "fea-result";
        case "glb":
        case "gltf":
            return "view";
        case "prof":
            return "profile";
        default:
            return "other";
    }
}

const FAMILY_COLOR: Record<Family, string> = {
    geometry: "text-sky-400",
    "fea-model": "text-violet-400",
    "fea-result": "text-emerald-400",
    view: "text-amber-400",
    profile: "text-rose-400",
    other: "text-gray-400",
};

const FileTypeIcon: React.FC<{name: string; className?: string}> = ({name, className = ""}) => (
    <svg
        width="14px"
        height="14px"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        className={`shrink-0 ${FAMILY_COLOR[familyOf(name)]} ${className}`}
    >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
    </svg>
);

export default FileTypeIcon;
