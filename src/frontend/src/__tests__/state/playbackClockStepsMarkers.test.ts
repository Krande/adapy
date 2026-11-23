// The playback clock's samples, steps and markers (state/playbackClock.ts), and a plugin's side of
// them (`ctx.playback`, plugins/sceneLayers.ts).
import assert from "node:assert/strict";
import { test } from "node:test";

import { makePluginPlayback, releaseOwnerLayers } from "@/plugins/sceneLayers";
import { PlaybackClock, playbackClock } from "@/state/playbackClock";

const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 1e-9, `${a} ≈ ${b}${msg ? `: ${msg}` : ""}`);

test("a frame step goes between the tracks' recorded samples, merged, and pauses", () => {
  const clock = new PlaybackClock();
  const shown: number[] = [];
  clock.addTrack({ id: "a", duration: 2, show: (t) => void shown.push(t), samples: [0, 1, 0.5] });
  const b = clock.addTrack({ id: "b", duration: 2, show: () => {}, samples: [0.004, 1] });
  assert.equal(clock.hasSamples(), true);
  assert.deepEqual([...clock.sampleTimes()], [0, 0.004, 0.5, 1]);
  clock.play();
  assert.equal(clock.step(1), 0.004, "the quick moment between two of the other track's samples");
  assert.equal(clock.state().playing, false, "a step pauses");
  assert.equal(shown[shown.length - 1], 0.004, "and the tracks show it");
  assert.equal(clock.step(1), 0.5);
  assert.equal(clock.step(-1, "frame", { shift: true }), 0, "ten back: as far as the first");
  // The recording grew: its samples with it.
  b.setSamples([0.004, 1, 1.5]);
  clock.seek(1);
  assert.equal(clock.step(1), 1.5);
  b.remove();
  assert.deepEqual([...clock.sampleTimes()], [0, 0.5, 1]);
});

test("a track that cannot list its samples answers the next one itself", () => {
  const clock = new PlaybackClock();
  const every = 0.03;
  clock.addTrack({
    id: "a",
    duration: 1,
    show: () => {},
    stepSample: (t, d) => {
      const i = d > 0 ? Math.floor(t / every + 1e-6) + 1 : Math.ceil(t / every - 1e-6) - 1;
      return i < 0 ? null : i * every;
    },
  });
  near(clock.step(1), 0.03);
  near(clock.step(1), 0.06);
  near(clock.step(-1), 0.03);
  assert.equal(clock.nextSample(0, -1), null);
});

test("without samples, a frame step is one display frame; a time step is that time, Shift ×10, Alt ÷10", () => {
  const clock = new PlaybackClock();
  clock.addTrack({ id: "a", duration: 10, show: () => {} });
  assert.equal(clock.hasSamples(), false);
  near(clock.frameSeconds(), 1 / 60);
  near(clock.step(1), 1 / 60);
  clock.seek(5);
  near(clock.step(1, 0.1), 5.1);
  near(clock.step(1, 0.1, { shift: true }), 6.1);
  near(clock.step(-1, 0.1, { alt: true }), 6.09);
  near(clock.step(1, 10), 10, "kept within the timeline");
  // The display's frame is measured while playing.
  clock.seek(0);
  clock.play();
  for (let ms = 0; ms <= 2000; ms += 1000 / 30) clock.tick(ms);
  assert.ok(Math.abs(clock.frameSeconds() - 1 / 30) < 0.002, String(clock.frameSeconds()));
});

test("markers: set per source, replaced, listed by time, cleared; the next and previous one, and going there", () => {
  const clock = new PlaybackClock();
  clock.addTrack({ id: "a", duration: 60, show: () => {} });
  let heard = 0;
  const off = clock.subscribe(() => heard++);
  clock.setMarkers("run", [
    { t: 30, label: "Weld pass 2", kind: "stage" },
    { t: 10, label: "Weld pass 1", kind: "stage" },
  ]);
  clock.setMarkers("checks", [{ t: 20, label: "Collision", kind: "error", color: "#f0f" }, { t: NaN, label: "bad" }, { t: 25, label: "Note", kind: "nope" as never }]);
  assert.deepEqual(
    clock.markers().map((m) => [m.t, m.label, m.kind, m.source]),
    [
      [10, "Weld pass 1", "stage", "run"],
      [20, "Collision", "error", "checks"],
      [25, "Note", "event", "checks"],
      [30, "Weld pass 2", "stage", "run"],
    ],
  );
  assert.equal(clock.markers()[1].color, "#f0f");
  assert.equal(clock.markers(), clock.markers(), "the same list until it changes");
  clock.seek(15);
  assert.equal(clock.nextMarker(15, 1)!.label, "Collision");
  assert.equal(clock.nextMarker(15, -1)!.label, "Weld pass 1");
  clock.play();
  assert.equal(clock.seekMarker(1)!.t, 20);
  assert.equal(clock.state().playing, false, "going to a marker pauses");
  assert.equal(clock.time(), 20);
  assert.equal(clock.seekMarker(1)!.t, 25, "from on a marker: the next one, not itself");
  assert.equal(clock.seekMarker(-1)!.t, 20);
  clock.seek(30);
  assert.equal(clock.seekMarker(1), null);
  assert.equal(clock.time(), 30, "none that way: the time stays");
  // Replaced, then cleared.
  clock.setMarkers("run", [{ t: 5, label: "Start" }]);
  assert.deepEqual(clock.markers().filter((m) => m.source === "run").map((m) => m.label), ["Start"]);
  clock.clearMarkers("run");
  clock.setMarkers("checks", []);
  assert.deepEqual(clock.markers(), []);
  const before = heard;
  clock.clearMarkers("run");
  assert.equal(heard, before, "nothing to clear: nothing to hear");
  off();
});

test("a plugin's markers and samples: namespaced, and gone with scene.remove(owner)", () => {
  const playback = makePluginPlayback("robot");
  const other = makePluginPlayback("other");
  const track = playback.addTrack({ id: "replay", duration: 12, show: () => {}, samples: [0, 0.02, 0.04] });
  try {
    playback.setMarkers("stages", [{ t: 2, label: "Approach", kind: "stage" }, { t: 6, label: "Weld", kind: "stage" }]);
    other.setMarkers("stages", [{ t: 4, label: "Theirs" }]);
    assert.deepEqual(
      playback.markers().map((m) => [m.source, m.label]),
      [
        ["robot:stages", "Approach"],
        ["other:stages", "Theirs"],
        ["robot:stages", "Weld"],
      ],
    );
    playbackClock.seek(0);
    assert.equal(playback.nextSample(0, 1), 0.02);
    assert.equal(playback.step(1), 0.02);
    track.setSamples([0, 0.01]);
    assert.equal(playback.nextSample(0, 1), 0.01);
    assert.equal(playback.seekMarker(1)!.label, "Approach");
    playback.clearMarkers("stages");
    assert.deepEqual(playback.markers().map((m) => m.label), ["Theirs"]);
    playback.setMarkers("stages", [{ t: 3, label: "Again" }]);
    releaseOwnerLayers("robot");
    assert.deepEqual(playback.markers().map((m) => m.label), ["Theirs"], "the plugin's went with it, the other's stayed");
    assert.equal(playbackClock.trackList().some((t) => t.id === "robot:replay"), false);
  } finally {
    track.remove();
    other.clearMarkers("stages");
  }
  assert.deepEqual(playbackClock.markers(), []);
});
