// Opening the Scene panel's Measure tab, from a command or a key (Shift+M), optionally with a tool in
// hand. Light on purpose: the key is bound by both viewports (viewport/api/viewportShortcuts.ts), so
// nothing here may pull three.js in. The main window's only: the key is bound where a viewport is,
// and the commands are offered in the main window (a pop-out has no scene to measure).
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import type {MeasureTool} from "@/state/measureStore";

import {reveal} from "./reveal";

const SCENE_PANEL = "core:scene";

/** Show the Scene panel on its Measure tab; with `tool`, take that tool up. */
export function openMeasure(tool?: MeasureTool): void {
    useSceneInfoStore.getState().setMode("measure");
    reveal(SCENE_PANEL);
    if (tool) void import("@/utils/measure/measureController").then((m) => m.armMeasureTool(tool));
}
