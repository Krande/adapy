// The transport's precise-scrubbing parts (components/playback/): its keys, its view (step size and
// zoom), what it draws (step buttons, markers, the precise time, the capture button) and the
// capture of a frame at an exact moment through the viewport on screen.
import assert from "node:assert/strict";
import { test } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { captureFrame } from "@/components/playback/frameCapture";
import PlaybackTransport, { runPlaybackKey } from "@/components/playback/PlaybackTransport";
import { keyBelongsElsewhere, playbackKeyAction } from "@/components/playback/playbackKeys";
import ScrubBar, { markerTitle } from "@/components/playback/ScrubBar";
import { timelineView } from "@/components/playback/timelineView";
import { useGalleryStore } from "@/state/galleryStore";
import { playbackClock } from "@/state/playbackClock";
import { setActiveViewport, type ViewportApi } from "@/viewport/api/viewportApi";

const key = (k: string, mods: Partial<{ shiftKey: boolean; altKey: boolean; ctrlKey: boolean; metaKey: boolean }> = {}, target?: unknown) => ({
  key: k,
  shiftKey: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  ...mods,
  target: (target ?? { tagName: "BODY" }) as EventTarget,
});

test("← / → step, Shift and Alt modify, [ / ] go to markers; Ctrl and other keys are not the transport's", () => {
  assert.deepEqual(playbackKeyAction(key("ArrowRight")), { kind: "step", direction: 1, shift: false, alt: false });
  assert.deepEqual(playbackKeyAction(key("ArrowLeft", { shiftKey: true })), { kind: "step", direction: -1, shift: true, alt: false });
  assert.deepEqual(playbackKeyAction(key("ArrowLeft", { altKey: true })), { kind: "step", direction: -1, shift: false, alt: true });
  assert.deepEqual(playbackKeyAction(key("]")), { kind: "marker", direction: 1 });
  assert.deepEqual(playbackKeyAction(key("[")), { kind: "marker", direction: -1 });
  assert.equal(playbackKeyAction(key("ArrowLeft", { ctrlKey: true })), null, "Ctrl+← orbits the camera");
  assert.equal(playbackKeyAction(key("ArrowUp")), null);
  assert.equal(playbackKeyAction(key("a")), null);
});

test("a field, a tree, a gallery walk's plain arrows keep their keys; the transport's own bar does not", () => {
  const step = playbackKeyAction(key("ArrowRight"))!;
  assert.equal(keyBelongsElsewhere(key("ArrowRight", {}, { tagName: "INPUT", type: "text" }), step), true);
  const inTree = { tagName: "DIV", closest: (sel: string) => (sel.includes("treeitem") ? {} : null) };
  assert.equal(keyBelongsElsewhere(key("ArrowRight", {}, inTree), step), true);
  const onBar = { tagName: "DIV", closest: () => ({}) };
  assert.equal(keyBelongsElsewhere(key("ArrowRight", {}, onBar), step), false, "the transport's own slider");
  assert.equal(keyBelongsElsewhere(key("ArrowRight"), step), false);
  useGalleryStore.getState().setEnabled(true);
  try {
    assert.equal(keyBelongsElsewhere(key("ArrowRight"), step), true, "the gallery walk's plain arrows");
    const coarse = playbackKeyAction(key("ArrowRight", { shiftKey: true }))!;
    assert.equal(keyBelongsElsewhere(key("ArrowRight", { shiftKey: true }), coarse), false);
  } finally {
    useGalleryStore.getState().setEnabled(false);
  }
});

test("the keys step by the chosen size and go to the markers", () => {
  const track = playbackClock.addTrack({ id: "keys", duration: 10, show: () => {}, samples: [0, 0.25, 0.5] });
  try {
    timelineView.reset();
    playbackClock.seek(0);
    runPlaybackKey({ kind: "step", direction: 1, shift: false, alt: false });
    assert.equal(playbackClock.time(), 0.25, "1 frame: the next sample");
    timelineView.setStepSize(1);
    runPlaybackKey({ kind: "step", direction: 1, shift: true, alt: false });
    assert.equal(playbackClock.time(), 10.0, "1 s ×10, kept within");
    runPlaybackKey({ kind: "step", direction: -1, shift: false, alt: true });
    assert.ok(Math.abs(playbackClock.time() - 9.9) < 1e-9);
    playbackClock.setMarkers("t", [{ t: 2, label: "A" }, { t: 4, label: "B" }]);
    runPlaybackKey({ kind: "marker", direction: -1 });
    assert.equal(playbackClock.time(), 4);
  } finally {
    playbackClock.clearMarkers("t");
    track.remove();
    timelineView.reset();
  }
});

test("the zoom is kept inside the timeline, and a window of all of it is no zoom", () => {
  timelineView.reset();
  timelineView.setZoom({ start: 2, end: 3 }, 10);
  assert.deepEqual(timelineView.get().zoom, { start: 2, end: 3 });
  assert.deepEqual(timelineView.window(2.5), { start: 1.5, end: 2.5 }, "a shorter timeline: moved inside it");
  timelineView.setZoom({ start: 0, end: 10 }, 10);
  assert.equal(timelineView.get().zoom, null);
  timelineView.setZoom({ start: 2, end: 3 }, 10);
  timelineView.resetZoom();
  assert.deepEqual(timelineView.window(10), { start: 0, end: 10 });
  timelineView.setStepSize(-1);
  assert.equal(timelineView.get().stepSize, "frame", "no such size");
});

test("the transport draws the step controls, the precise time, the markers and the capture button", () => {
  const track = playbackClock.addTrack({ id: "draw", duration: 83.5, show: () => {} });
  try {
    timelineView.reset();
    playbackClock.seek(61.25);
    let html = renderToStaticMarkup(<PlaybackTransport />);
    assert.match(html, /aria-label="Step back"/);
    assert.match(html, /aria-label="Step forward"/);
    assert.match(html, /aria-label="Step size"/);
    assert.match(html, /<option value="frame" selected="">1 frame<\/option>/);
    assert.match(html, /title="01:01\.250 of 01:23\.500\. Click to type a time to go to"[^>]*>01:01\.250</);
    assert.match(html, /aria-label="Capture frame"/);
    assert.match(html, /type="range"[^>]*aria-label="Time"/, "a plain range, for the keyboard, a screen reader and automation");
    assert.doesNotMatch(html, /aria-label="Next marker"/, "no markers: no marker buttons");
    assert.doesNotMatch(html, /aria-label="Reset zoom"/, "not zoomed: nothing to reset");
    playbackClock.setMarkers("run", [{ t: 10, label: "Weld pass 1", kind: "stage" }, { t: 70, label: "Contact", kind: "error" }]);
    timelineView.setZoom({ start: 60, end: 80 }, 83.5);
    html = renderToStaticMarkup(<PlaybackTransport />);
    assert.match(html, /aria-label="Next marker"/);
    assert.match(html, /aria-label="Reset zoom"/);
    assert.match(html, /01:00\.000–01:20\.000/);
    assert.match(html, /title="Contact · 01:10\.000"[^>]*data-playback-marker="error"/);
    assert.doesNotMatch(html, /Weld pass 1/, "a marker outside the zoomed window is not drawn");
  } finally {
    playbackClock.clearMarkers("run");
    track.remove();
    timelineView.reset();
  }
});

test("the bar places the handle and the markers on the window it shows", () => {
  const markers = [{ t: 15, label: "Mid", kind: "stage" as const, source: "s" }];
  const html = renderToStaticMarkup(
    <ScrubBar time={12.5} duration={100} view={{ start: 10, end: 20 }} markers={markers} onSeek={() => {}} onZoom={() => {}} />,
  );
  assert.match(html, /style="left:25\.000%" data-playback-handle="true"/);
  assert.match(html, /style="left:50\.000%"[^>]*title="Mid · 00:15\.000"/);
  assert.equal(markerTitle({ t: 1, label: "", kind: "warning", source: "s" }), "warning · 00:01.000");
});

test("capture frame pauses, goes to the moment, lets a frame be drawn and saves it named by its time", async () => {
  const saved: (string | undefined)[] = [];
  const api = { kind: "native", saveScreenshot: (name?: string) => void saved.push(name) } as unknown as ViewportApi;
  const track = playbackClock.addTrack({ id: "cap", duration: 5, show: () => {} });
  const withdraw = setActiveViewport(api);
  try {
    playbackClock.play();
    const name = await captureFrame({ time: 1.2345 });
    assert.equal(playbackClock.state().playing, false);
    assert.equal(name, "frame_t00m01.235s.png");
    assert.deepEqual(saved, ["frame_t00m01.235s.png"]);
    playbackClock.seek(2);
    assert.equal(await captureFrame({ prefix: "impact" }), "impact_t00m02.000s.png", "default: the time now");
  } finally {
    withdraw();
    track.remove();
  }
  assert.equal(await captureFrame(), null, "no viewport: nothing saved");
});
