import React from "react";
import StorageBrowser from "./StorageBrowser";

// The file browser as the shell's dock hosts it: no frame, no repeated title.
//
// A wrapper rather than a prop on the registry entry, because the registry's component
// type takes no props — and giving it props would let any panel demand configuration the
// registry has no way to describe.
export default function StoragePanel() {
    return <StorageBrowser chromeless />;
}
