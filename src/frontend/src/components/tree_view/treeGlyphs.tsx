// The glyphs the left-panel trees (Scene and Sources) draw beside their rows -- one set, so a level
// looks the same in both. Hand-drawn SVG in `currentColor`: no icon package.

import React from "react";

export const Chevron: React.FC<{ open: boolean }> = ({ open }) => (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="currentColor">
        {open ? <path d="M2 3l3 4 3-4z" /> : <path d="M3 2l4 3-4 3z" />}
    </svg>
);

/** A level (folder) or a piece of geometry (cube). */
export const NodeGlyph: React.FC<{ branch: boolean }> = ({ branch }) => (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" className="shrink-0" fill="currentColor" stroke="currentColor">
        {branch ? (
            <path d="M1.5 3.5h4.2l1.5 1.6h7.3v8.4h-13z" fillOpacity="0.18" strokeWidth="1.2" strokeLinejoin="round" />
        ) : (
            <g strokeWidth="1.1" strokeLinejoin="round">
                <path d="M8 1.8 13.6 5v6.2L8 14.4 2.4 11.2V5z" fillOpacity="0.18" />
                <path d="M2.4 5 8 8.2 13.6 5M8 8.2v6.2" fill="none" />
            </g>
        )}
    </svg>
);

/** A loaded model: the root of everything it holds (a stack of layers). */
export const ModelGlyph: React.FC = () => (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" className="shrink-0" fill="currentColor" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round">
        <path d="M8 1.8 14.2 5 8 8.2 1.8 5z" fillOpacity="0.25" />
        <path d="M1.8 8 8 11.2 14.2 8" fill="none" />
        <path d="M1.8 11 8 14.2 14.2 11" fill="none" />
    </svg>
);

/** Visibility: an open eye, or one struck through. */
export const EyeGlyph: React.FC<{ shut: boolean }> = ({ shut }) => (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z" />
        <circle cx="8" cy="8" r="2" />
        {shut && <path d="M2.5 13.5 13.5 2.5" />}
    </svg>
);
