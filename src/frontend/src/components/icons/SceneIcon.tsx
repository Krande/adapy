import React from "react";

// Stacked-layers metaphor — the Scene panel is a container for everything
// loaded in the viewer (multiple models, their stats, their groups), so
// three layered diamonds read more naturally than the old people-group glyph.
const SceneIcon = () => {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3 2 8l10 5 10-5-10-5Z"/>
            <path d="M2 13l10 5 10-5"/>
            <path d="M2 18l10 5 10-5"/>
        </svg>
    );
};

export default SceneIcon;
