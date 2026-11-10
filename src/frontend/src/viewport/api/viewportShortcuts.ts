// The core keys whose action is the viewport's: bound once here, to whichever viewport is on screen, so
// the three.js viewport and the three.js-free one answer the same keys. No three.js in this module.
import {CAMERA_VIEWS, cameraViewCommandId} from "@/layout/coreCommandIds";
import type {CoreShortcutHandler} from "@/layout/shortcuts";
import {activeViewport} from "./viewportApi";

type Run = () => void | boolean;

/** Handlers by core shortcut id. `wrap` gives each the caller's own `when` (its scope, its focus rules).
 *  A key with no viewport on screen, or for something it cannot do, is left to the browser. */
export function viewportShortcutHandlers(wrap: (run: Run) => CoreShortcutHandler): Record<string, CoreShortcutHandler> {
    const on = (act: (v: NonNullable<ReturnType<typeof activeViewport>>) => void | boolean): CoreShortcutHandler =>
        wrap(() => {
            const viewport = activeViewport();
            return viewport ? act(viewport) : false;
        });
    return {
        "core:hide-selected": on((v) => v.hideSelected()),
        "core:hide-others": on((v) => v.hideOthers()),
        "core:show-all": on((v) => v.showAll()),
        "core:fit-all": on((v) => v.fitAll()),
        "core:fit-selection": on((v) => (v.unavailable("fit-selection") ? false : v.fitSelection())),
        "core:reset-camera": on((v) => v.resetCamera()),
        ...Object.fromEntries(CAMERA_VIEWS.map((view) => [cameraViewCommandId(view), on((v) => v.setView(view))])),
        "core:orbit-left": on((v) => v.orbitStep(-1, 0)),
        "core:orbit-right": on((v) => v.orbitStep(1, 0)),
        "core:orbit-up": on((v) => v.orbitStep(0, 1)),
        "core:orbit-down": on((v) => v.orbitStep(0, -1)),
        "core:pan-left": on((v) => v.panStep(-1, 0)),
        "core:pan-right": on((v) => v.panStep(1, 0)),
        "core:pan-up": on((v) => v.panStep(0, 1)),
        "core:pan-down": on((v) => v.panStep(0, -1)),
    };
}
