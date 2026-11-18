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
}

export interface PluginPlaybackTrackHandle {
  // The recording grew: the clock's duration is its longest track's.
  setDuration: (seconds: number) => void;
  remove: () => void;
}

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
