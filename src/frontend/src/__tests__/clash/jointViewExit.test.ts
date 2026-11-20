// A joint shown with the rest of the model faded ("fade rest") is left by Esc -- not while typing
// in a field -- or by a left click on empty space; a drag that orbits is not a click. Leaving clears
// the joint focus (and an open group) as the panels do, and the fade comes off: full opacity again.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import type { ContactRunDoc } from "@/services/clash/contactRuns";
import type { ObjectFade } from "@/viewport/api/objectFade";
import type { ViewportApi } from "@/viewport/api/viewportApi";

await import("../fixtures/panelRealm");

const { contactRunToResult, useClashCheckStore } = await import("@/state/clashCheckStore");
const { useModelState } = await import("@/state/modelState");
const markers = await import("@/utils/scene/clashJointMarkers");
const { exitJointView, installJointViewExit, isJointViewExitKey, jointViewActive } = await import("@/utils/scene/jointViewExit");
const { notifyViewportPick, setActiveViewport } = await import("@/viewport/api/viewportApi");

type Vec3 = [number, number, number];
const MODEL = "models/frame.ifc";

function run(): ContactRunDoc {
  const sq = (px: number): Vec3[] => [
    [px, 0, 0],
    [px, 1, 0],
    [px, 1, 1],
    [px, 0, 0],
  ];
  return {
    schema: "ada.clash/contacts@1",
    run_id: "r1",
    created_at: "2026-01-01T00:00:00Z",
    engine: "wasm",
    checker: "convex-contact",
    target: { kind: "file", source_key: MODEL },
    geometry: { key: "_derived/models/frame.ifc.glb", sha256: "00", up_axis: "z" },
    options: { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 },
    members: [
      { id: "4", name: "column_1" },
      { id: "5", name: "brace_1" },
    ],
    contacts: [
      {
        id: "c0",
        members: [0, 1],
        near_points: [
          [0.2, 0.1, 0.5],
          [0.198, 0.1, 0.5],
        ],
        normal: [1, 0, 0],
        distance: -0.002,
        contact_area: 0.02,
        patches: [{ normal: [1, 0, 0], points1: sq(0.2), points2: sq(0.198) }],
      },
    ],
    provenance: {},
  } as ContactRunDoc;
}

function fadingViewport() {
  const fades: (ObjectFade | null)[] = [];
  const layer = { addPoints: () => {}, addLines: () => {}, addMesh: () => {}, clear: () => {}, dispose: () => {} };
  const viewport = {
    kind: "native",
    sourceOrigin: () => [0, 0, 0],
    modelBounds: () => [0, 0, 0, 10, 10, 10],
    overlays: () => layer,
    clearModels: () => {},
    setObjectFade: (fade: ObjectFade | null) => void fades.push(fade),
  } as unknown as ViewportApi;
  return { viewport, faded: () => fades.at(-1) ?? null };
}

/** A window stand-in: the keydown listeners, and a key pressed in `target`. */
function fakeWindow() {
  const listeners = new Set<(e: unknown) => void>();
  return {
    addEventListener: (_t: string, l: (e: unknown) => void) => void listeners.add(l),
    removeEventListener: (_t: string, l: (e: unknown) => void) => void listeners.delete(l),
    press(key: string, target: unknown = { tagName: "CANVAS" }, mods: Partial<Record<"shiftKey" | "ctrlKey" | "altKey" | "metaKey", boolean>> = {}) {
      const e = {
        key,
        target,
        shiftKey: false,
        ctrlKey: false,
        altKey: false,
        metaKey: false,
        ...mods,
        defaultPrevented: false,
        preventDefault() {
          this.defaultPrevented = true;
        },
      };
      listeners.forEach((l) => l(e));
      return e.defaultPrevented;
    },
  };
}

function showJoint() {
  useModelState.getState().registerLoadedSourceName(MODEL);
  useClashCheckStore.setState({
    result: contactRunToResult(run(), null),
    sourceName: MODEL,
    isolate: "ghost",
    isolateOpacity: 0.2,
    selectedGroup: null,
    selectedJoint: null,
  });
  useClashCheckStore.getState().focusJoint("c0");
}

let cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.forEach((c) => c());
  cleanups = [];
  useClashCheckStore.setState({ result: null, selectedJoint: null, selectedGroup: null, isolate: "ghost", isolateOpacity: 0.15 });
  useModelState.getState().clearLoadedSources?.();
});

test("Esc leaves the faded view: the focus is cleared and the fade comes off", () => {
  const rec = fadingViewport();
  cleanups.push(setActiveViewport(rec.viewport));
  const win = fakeWindow();
  cleanups.push(installJointViewExit(win));
  markers.startClashMarkerSync();
  showJoint();
  assert.ok(jointViewActive());
  assert.ok(rec.faded(), "the rest is faded");

  assert.equal(win.press("Escape"), true, "Esc is taken");
  assert.equal(useClashCheckStore.getState().selectedJoint, null);
  assert.equal(rec.faded(), null, "full opacity again");
  // The mode stays as the user set it, for the next joint.
  assert.equal(useClashCheckStore.getState().isolate, "ghost");

  // Nothing to leave: Esc is not taken.
  assert.equal(win.press("Escape"), false);
});

test("Esc typed in a field, or with a modifier, does not leave the view", () => {
  const win = fakeWindow();
  cleanups.push(installJointViewExit(win));
  showJoint();
  for (const target of [{ tagName: "INPUT" }, { tagName: "TEXTAREA" }, { tagName: "SELECT" }, { tagName: "DIV", isContentEditable: true }]) {
    assert.equal(win.press("Escape", target), false);
  }
  assert.equal(win.press("Escape", { tagName: "CANVAS" }, { shiftKey: true }), false);
  assert.equal(win.press("x"), false);
  assert.equal(useClashCheckStore.getState().selectedJoint, "c0", "still focused");
  assert.equal(isJointViewExitKey({ key: "Escape", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, target: null }), true);
});

test("with the rest shown as it is, Esc leaves the joint focused", () => {
  const win = fakeWindow();
  cleanups.push(installJointViewExit(win));
  showJoint();
  useClashCheckStore.getState().setIsolate("off");
  assert.equal(win.press("Escape"), false);
  assert.equal(useClashCheckStore.getState().selectedJoint, "c0");
});

test("a click on empty space leaves the view; a click on an object does not", () => {
  const win = fakeWindow();
  cleanups.push(installJointViewExit(win));
  showJoint();
  notifyViewportPick({ ref: { model: "m1", id: "4" } });
  assert.equal(useClashCheckStore.getState().selectedJoint, "c0");
  notifyViewportPick({ ref: null });
  assert.equal(useClashCheckStore.getState().selectedJoint, null);
});

test("an open group faded on its own is left too", () => {
  showJoint();
  const typeKey = useClashCheckStore.getState().selectedGroup;
  assert.ok(typeKey, "focusing the joint opened its group");
  useClashCheckStore.getState().focusJoint(null);
  assert.ok(jointViewActive(), "the group alone still fades the rest");
  assert.equal(exitJointView(), true);
  assert.equal(useClashCheckStore.getState().selectedGroup, null);
  assert.equal(exitJointView(), false);
});

test("on the three.js viewport, a left click on empty space leaves the view and a drag does not", async () => {
  const THREE = await import("three");
  const { setupPointerHandler } = await import("@/components/viewer/sceneHelpers/setupPointerHandler");
  const handlers = new Map<string, ((e: unknown) => unknown)[]>();
  const container = {
    addEventListener: (type: string, l: (e: unknown) => unknown) => handlers.set(type, [...(handlers.get(type) ?? []), l]),
  } as unknown as HTMLDivElement;
  const renderer = { domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) } };
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.set(0, 0, 10);
  camera.updateMatrixWorld();
  const dispose = setupPointerHandler(container, camera, new THREE.Scene(), renderer as never);
  cleanups.push(dispose);
  const fire = async (type: string, e: Record<string, unknown>) => {
    for (const l of handlers.get(type) ?? []) await l({ pointerType: "mouse", button: 0, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, timeStamp: 0, ...e });
  };
  showJoint();

  // A drag (pressed here, released 60 px away) orbits: not a click.
  await fire("pointerdown", { clientX: 10, clientY: 10 });
  await fire("click", { clientX: 70, clientY: 70, timeStamp: 1000 });
  assert.equal(useClashCheckStore.getState().selectedJoint, "c0");

  // A click where it was pressed, on nothing: the view is left.
  await fire("pointerdown", { clientX: 50, clientY: 50 });
  await fire("click", { clientX: 51, clientY: 50, timeStamp: 5000 });
  assert.equal(useClashCheckStore.getState().selectedJoint, null);
});
