// The viewport interface: which viewport is the one on screen, and the keys bound through it.
import {test} from "node:test";
import assert from "node:assert/strict";

import {CAMERA_VIEWS, cameraViewCommandId} from "@/layout/coreCommandIds";
import {CORE_SHORTCUTS} from "@/layout/coreShortcutTable";
import {activeViewport, onActiveViewportChange, setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import {viewportShortcutHandlers} from "@/viewport/api/viewportShortcuts";

function recorder(kind: ViewportApi["kind"], cannot: string | null = null): {api: ViewportApi; calls: string[]} {
    const calls: string[] = [];
    const log =
        (name: string) =>
        (...args: unknown[]) =>
            void calls.push(args.length ? `${name}(${args.join(",")})` : name);
    return {
        calls,
        api: {
            kind,
            unavailable: () => cannot,
            selectionCount: () => 0,
            selection: () => [],
            selectionToken: () => calls.length,
            onSelectionChange: () => () => {},
            setSelection: (...args: unknown[]) => (log("setSelection")(...args), 0),
            modelBounds: () => null,
            results: {
                createResultMesh: () => {
                    throw new Error("not drawn in this test");
                },
            },
            clearSelection: log("clearSelection"),
            hideSelected: log("hideSelected"),
            hideOthers: log("hideOthers"),
            hide: log("hide"),
            saveScreenshot: log("saveScreenshot"),
            unloadSource: () => false,
            show: log("show"),
            isolate: log("isolate"),
            objectVisibility: () => () => null,
            showAll: log("showAll"),
            cameraPose: () => null,
            setCameraPose: log("setCameraPose"),
            fitAll: log("fitAll"),
            fitSelection: log("fitSelection"),
            setView: log("setView"),
            resetCamera: log("resetCamera"),
            orbitStep: log("orbitStep"),
            panStep: log("panStep"),
            objectsCentre: () => null,
            billboards: () => {
                throw new Error("no billboards in this test");
            },
        },
    };
}

test("the viewport on screen is the last one registered, until it withdraws", () => {
    assert.equal(activeViewport(), null);
    let changes = 0;
    const off = onActiveViewportChange(() => changes++);
    const three = recorder("three");
    const native = recorder("native");

    const withdrawThree = setActiveViewport(three.api);
    assert.equal(activeViewport(), three.api);
    const withdrawNative = setActiveViewport(native.api);
    assert.equal(activeViewport(), native.api);

    // The replaced viewport unmounting afterwards must not take the new one down with it.
    withdrawThree();
    assert.equal(activeViewport(), native.api);
    withdrawNative();
    assert.equal(activeViewport(), null);
    assert.equal(changes, 3);

    off();
    setActiveViewport(three.api)();
    assert.equal(changes, 3, "an unsubscribed listener hears nothing");
});

test("the viewport's keys act on whichever viewport is on screen", () => {
    const handlers = viewportShortcutHandlers((run) => ({run}));
    const first = recorder("three");
    const second = recorder("native");

    const withdrawFirst = setActiveViewport(first.api);
    handlers["core:hide-selected"].run();
    handlers["core:orbit-left"].run();
    withdrawFirst();

    const withdrawSecond = setActiveViewport(second.api);
    handlers["core:show-all"].run();
    handlers["core:pan-up"].run();
    handlers[cameraViewCommandId("top")].run();
    withdrawSecond();

    assert.deepEqual(first.calls, ["hideSelected", "orbitStep(-1,0)"]);
    assert.deepEqual(second.calls, ["showAll", "panStep(0,1)", "setView(top)"]);
});

test("a key with no viewport, or for something the viewport cannot do, is left to the browser", () => {
    const handlers = viewportShortcutHandlers((run) => ({run}));
    assert.equal(handlers["core:fit-all"].run(), false, "no viewport on screen");

    const limited = recorder("native", "not yet");
    const withdraw = setActiveViewport(limited.api);
    assert.equal(handlers["core:fit-selection"].run(), false);
    assert.notEqual(handlers["core:fit-all"].run(), false);
    withdraw();
    assert.deepEqual(limited.calls, ["fitAll"]);
});

test("every key bound through the interface is a dispatched core shortcut, and the caller's scope is kept", () => {
    const when = () => false;
    const handlers = viewportShortcutHandlers((run) => ({run, when}));
    const dispatched = new Set(CORE_SHORTCUTS.filter((s) => !s.local).map((s) => s.id));
    for (const [id, handler] of Object.entries(handlers)) {
        assert.ok(dispatched.has(id), `${id} is not a dispatched core shortcut`);
        assert.equal(handler.when, when);
    }
    for (const view of CAMERA_VIEWS) assert.ok(handlers[cameraViewCommandId(view)], `no key handler for the ${view} view`);
});
