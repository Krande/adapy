// An FEA source opened as the websocket connects (`ada view result.SIN`) can arrive
// before the canvas mounts. The load must wait for the scene rather than run on none,
// which left the bare mesh on screen with no FEA session behind it.
import {test} from "node:test";
import assert from "node:assert/strict";
import type * as THREE from "three";

import {createViewerRuntime, mountViewerRuntime, waitForViewerScene, waitForViewport} from "@/state/viewerRuntime";

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

test("waitForViewerScene gives up at once when no three.js scene will come", async () => {
    const started = Date.now();
    await assert.rejects(waitForViewerScene(5_000, () => true), /three\.js-free viewport/);
    assert.ok(Date.now() - started < 1_000, "it did not wait for the timeout");
});

// Before either viewport has mounted, the load cannot yet tell which path to take: it waits for
// whichever comes up, so a result opened then still lands on the three.js-free viewport.
test("waitForViewport resolves when the other viewport comes up first", async () => {
    const runtime = createViewerRuntime();
    const unmount = mountViewerRuntime(runtime);
    try {
        let otherUp = false;
        setTimeout(() => (otherUp = true), 40);
        await waitForViewport(() => otherUp, 2_000);
        assert.equal(runtime.scene.current, null);
    } finally {
        unmount();
    }
});

test("waitForViewport resolves when the three.js scene comes up first", async () => {
    const runtime = createViewerRuntime();
    const unmount = mountViewerRuntime(runtime);
    try {
        setTimeout(() => {
            (runtime.scene as {current: THREE.Scene | null}).current = {} as THREE.Scene;
        }, 40);
        await waitForViewport(() => false, 2_000);
    } finally {
        unmount();
    }
});

test("waitForViewport rejects when neither comes up in time", async () => {
    const runtime = createViewerRuntime();
    const unmount = mountViewerRuntime(runtime);
    try {
        await assert.rejects(waitForViewport(() => false, 50), /no viewport ready/);
    } finally {
        unmount();
    }
});
