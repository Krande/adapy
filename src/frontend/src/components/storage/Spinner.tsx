import React from "react";

// Small inline CSS spinner. Uses border tricks rather than an SVG so it scales with text
// size and stays crisp at 16px tall icons. Shared by the file rows, the version tree and
// the upload button.
export const Spinner: React.FC<{className?: string}> = ({className = ""}) => (
    <span
        className={`inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
        aria-hidden="true"
    />
);
