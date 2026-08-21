import React from "react";
import SceneBody, {useSceneContextTabs} from "./SceneBody";

// The Scene panel as the shell's dock hosts it: content only, no frame.
//
// A three-line file rather than pointing the registry straight at SceneBody, because the
// contextual-tab question ("does this model have FE concepts / joints") is state the body
// should not have to know how to ask. Both entry points get the same answer from the same
// hook, so the classic panel and the docked one can never disagree about which tabs exist.

export default function ScenePanel() {
    return <SceneBody ctxAvailable={useSceneContextTabs()} />;
}
