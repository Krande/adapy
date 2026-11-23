// The plugin API's playback clock, layer and simulation-subject handles. Types only: part of the
// plugin API `../registry` versions and re-exports.



// ── The playback clock (1.18.0) ───────────────────────────────────────────────

/** Something timed: the clock calls `show` whenever its time changes, once per frame while it plays. */
export interface PluginPlaybackTrack {
  id: string; // unique within the plugin
  duration: number; // seconds
  // Show the moment `t` (seconds, 0..duration). Runs in the frame loop: it must not allocate.
  show: (t: number) => void;
  label?: string;
  // (1.39.0) The times (seconds) the track has recorded samples at, e.g. a replay's frame times: the
  // transport's frame step (←/→ with "1 frame") goes from one to the next, so a quick moment is not
  // stepped over. Left out (and no `stepSample`): a frame step is one display frame.
  samples?: ArrayLike<number>;
  // (1.39.0) For a track that cannot list its samples: the sample after (direction 1) or before (-1)
  // `t`, or null for none that way.
  stepSample?: (t: number, direction: 1 | -1) => number | null;
}

export interface PluginPlaybackTrackHandle {
  // The recording grew: the clock's duration is its longest track's.
  setDuration: (seconds: number) => void;
  // (1.39.0) The sample times changed (a recording still being written); null: none.
  setSamples: (times: ArrayLike<number> | null) => void;
  remove: () => void;
}

/** (1.39.0) What a marker stands for; the scrub bar draws its tick in the kind's colour. */
export type PluginPlaybackMarkerKind = "stage" | "event" | "warning" | "error";

/** (1.39.0) A labelled moment on the scrub bar: a tick with `label` as its tooltip, a click on
 *  which seeks to `t`. */
export interface PluginPlaybackMarker {
  t: number; // seconds on the clock
  label: string;
  kind?: PluginPlaybackMarkerKind; // default "event"
  color?: string; // a CSS colour, over the kind's
}

/** (1.39.0) A marker as `markers()` lists it: `source` is `<pluginId>:<sourceId>` (core's own
 *  start with `core:`), `kind` always set. */
export interface PluginPlaybackMarkerEntry extends PluginPlaybackMarker {
  source: string;
  kind: PluginPlaybackMarkerKind;
}

/** (1.39.0) A step of the transport: `"frame"` is one recorded sample (a display frame where no
 *  track has samples); a number is seconds. */
export type PluginPlaybackStepSize = "frame" | number;

/** The clock as a value; a new object whenever any of it changed. */
export interface PluginPlaybackState {
  readonly duration: number; // seconds: the longest track's
  readonly time: number;
  readonly playing: boolean;
  readonly speed: number; // 1 is real time
  readonly loop: boolean;
  readonly followEnd: boolean;
  readonly tracks: number; // every registered track, core's and other plugins' included
}

/** The viewer's one playback clock. A plugin's tracks go with `ctx.scene.remove(pluginId)`. */
export interface PluginPlayback {
  addTrack: (track: PluginPlaybackTrack) => PluginPlaybackTrackHandle;
  state: () => PluginPlaybackState;
  // The time now, without making a state object: for a frame.
  time: () => number;
  // Called when the state changed; while playing, the moving time about ten times a second.
  subscribe: (listener: () => void) => () => void;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  setSpeed: (speed: number) => void;
  setLoop: (loop: boolean) => void;
  // Keep the time at the end while the duration grows (a recording still being written).
  // Turning it on goes to the end; `seek` and `pause` let go.
  followEnd: (follow: boolean) => void;

  // ── 1.39.0: markers, steps, frame capture. An older core has none of these: ask first
  //    (`typeof ctx.playback.setMarkers === "function"`, or PLUGIN_API_VERSION >= 1.39.0).

  // Put `markers` on the scrub bar for `sourceId` (unique within the plugin), in place of what
  // that source had there; an empty list clears it. They go with `ctx.scene.remove(pluginId)`.
  setMarkers: (sourceId: string, markers: readonly PluginPlaybackMarker[]) => void;
  clearMarkers: (sourceId: string) => void;
  // Every source's markers, core's and other plugins' included, by time.
  markers: () => readonly PluginPlaybackMarkerEntry[];
  // Pause and go to the next (1) or previous (-1) marker; the marker, or null for none that way.
  seekMarker: (direction: 1 | -1) => PluginPlaybackMarkerEntry | null;
  // Pause and step by `size` (default "frame"), Shift ×10 / Alt ÷10 as the keys do. The time
  // stepped to.
  step: (direction: 1 | -1, size?: PluginPlaybackStepSize, mods?: { shift?: boolean; alt?: boolean }) => number;
  // The nearest recorded sample of any track after (1) or before (-1) `t`; null for none.
  nextSample: (t: number, direction: 1 | -1) => number | null;
  // Pause, go to `time` (default: now), let a frame be drawn and save the view as
  // "<prefix>_t<mm>m<ss.mmm>s.png" (prefix default "frame"). The file's name; null with no viewport.
  captureFrame: (options?: { time?: number; prefix?: string }) => Promise<string | null>;
}

/** One of the plugin's rows in the Layers panel, as it is now. */
export interface PluginLayerState {
  id: string;
  label: string;
  group?: string;
  visible: boolean;
  opacity: number;
}

/** The plugin's rows of the Layers panel (the `layer` its drawn things were given). */
export interface PluginLayers {
  list: () => PluginLayerState[];
  setVisible: (id: string, visible: boolean) => void;
  setOpacity: (id: string, opacity: number) => void;
  subscribe: (listener: () => void) => () => void;
}

// ── Simulation subjects (1.21.0) ──────────────────────────────────────────────

/** Something a simulation run can be started on, offered to the Simulations panel. */
export interface PluginSimulationSubject {
  /** One of the server's subject kinds (`ada.simulation.subjects.SUBJECT_KINDS`): `"weld-assembly"`. */
  kind: string;
  /** Storage key of the stored file. */
  key: string;
  /** What a person calls it. */
  label: string;
  /** The scope the file is in (`ctx.scope()` when left out). */
  scope?: string;
  /** Who offered it, for a person; the plugin's id when left out. */
  source?: string;
}

/** The offer, to take back. */
export interface PluginSimulationSubjectHandle {
  withdraw: () => void;
}

/** What a plugin may do with simulations beside registering a provider's UI. */
export interface PluginSimulations {
  /**
   * Offer a stored file as something to simulate. The Simulations panel lists it in a "Subject"
   * picker (the newest offer chosen first) and starts a run on it with a provider that accepts its
   * kind. The plugin writes the file in the kind's format and need not know which provider will
   * take it, nor the provider the plugin.
   */
  offerSubject: (subject: PluginSimulationSubject) => PluginSimulationSubjectHandle;
}
