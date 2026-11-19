// An FEA source opened as the websocket connects (`ada view result.SIN`) can arrive
// before the canvas mounts. The load must wait for the scene rather than run on none,
// which left the bare mesh on screen with no FEA session behind it.
import {test} from "node:test";
import assert from "node:assert/strict";
import type * as THREE from "three";

import {createViewerRuntime, mountViewerRuntime, waitForViewerScene} from "@/state/viewerRuntime";

test("waitForViewerScene resolves once the canvas sets the scene", async () => {
    const runtime = createViewerRuntime();
    const unmount = mountViewerRuntime(runtime);
    try {
        const scene = {} as THREE.Scene;
        setTimeout(() => {
            (runtime.scene as {current: THREE.Scene | null}).current = scene;
        }, 40);
        assert.equal(await waitForViewerScene(2_000), scene);
    } finally {
        unmount();
    }
});

test("waitForViewerScene rejects when no scene appears in time", async () => {
    const runtime = createViewerRuntime();
    const unmount = mountViewerRuntime(runtime);
    try {
        await assert.rejects(waitForViewerScene(50), /not ready/);
    } finally {
        unmount();
    }
});
