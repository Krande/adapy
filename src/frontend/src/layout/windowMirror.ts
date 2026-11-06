// Mirroring the main window's state to pop-out windows, so a panel that reads
// the scene can live in a window without one (`capabilities.mirrored`).
//
// The main window owns the scene. Over the window bus (`./windowBus`) it
// PUBLISHES serialisable snapshots of what mirrored panels read, one TOPIC per
// piece of state (the selection, say), whenever that state changes and whenever
// a window asks; a pop-out window APPLIES each snapshot to its own copy of the
// store, so the panel renders as it would in the main window. What a panel DOES
// in a pop-out goes the other way, as a COMMAND the main window runs on its own
// state -- and then publishes again. Three.js objects never cross: a topic's
// snapshot is plain data, and a command's arguments are ids and values.
//
// Topics and commands are registered once per page (core's in
// `./windowMirrorCore`, a plugin's beside its own store); the main window hosts
// them (`hostMirror`), a pop-out window follows them (`followMirror`).
//
// Dependency-free.

import { MAIN_WINDOW_ID, type WindowBus, type WindowId } from "./windowBus";

/** One piece of state the main window shares. */
export interface MirrorTopic<T = unknown> {
  /** Unique, e.g. `"core:selection"` or `"<pluginId>:<name>"`. */
  id: string;
  /** In the main window: the state now, as plain (structured-clone safe) data. */
  snapshot(): T;
  /** In the main window: notifies when `snapshot()` may have changed. Returns the unsubscribe. */
  subscribe(cb: () => void): () => void;
  /** In a pop-out window: put a snapshot into this window's copy of the state. */
  apply(data: T): void;
}

/** An action a pop-out window asks the main window to run. */
export interface MirrorCommand<A = unknown> {
  /** Unique, e.g. `"core:select-node"`. */
  id: string;
  /** In the main window. */
  run(args: A, from: WindowId): void;
}

const topics = new Map<string, MirrorTopic>();
const commands = new Map<string, MirrorCommand>();
const registryListeners = new Set<() => void>();

/** Share a piece of state with pop-out windows. A later registration under the same id replaces it. */
export function registerMirrorTopic<T>(topic: MirrorTopic<T>): () => void {
  topics.set(topic.id, topic as MirrorTopic);
  for (const l of [...registryListeners]) l();
  return () => {
    if (topics.get(topic.id) === (topic as MirrorTopic)) topics.delete(topic.id);
  };
}

/** Let pop-out windows run `command` in the main window. */
export function registerMirrorCommand<A>(command: MirrorCommand<A>): () => void {
  commands.set(command.id, command as MirrorCommand);
  return () => {
    if (commands.get(command.id) === (command as MirrorCommand)) commands.delete(command.id);
  };
}

export function listMirrorTopics(): MirrorTopic[] {
  return [...topics.values()];
}

/** Clear the registries -- tests only. */
export function resetMirror(): void {
  topics.clear();
  commands.clear();
}

/**
 * The main window's side: publish every topic on change (coalesced to one
 * message per topic per `schedule` tick) and on request, and run the commands
 * pop-outs send. Topics registered later are hosted too. Returns the uninstall.
 */
export function hostMirror(bus: WindowBus, schedule: (fn: () => void) => void = queueMicrotask): () => void {
  const seq = new Map<string, number>();
  const dirty = new Set<string>();
  let queued = false;
  const publish = (id: string) => {
    const t = topics.get(id);
    if (!t) return;
    let data: unknown;
    try {
      data = t.snapshot();
    } catch (err) {
      console.warn(`[windows] the mirror topic "${id}" failed`, err);
      return;
    }
    const n = (seq.get(id) ?? 0) + 1;
    seq.set(id, n);
    bus.post({ type: "mirror", topic: id, data, seq: n });
  };
  const flush = () => {
    queued = false;
    const ids = [...dirty];
    dirty.clear();
    for (const id of ids) publish(id);
  };
  const mark = (id: string) => {
    // Nobody to tell: no message at all.
    if (!bus.windows().length) return;
    dirty.add(id);
    if (!queued) {
      queued = true;
      schedule(flush);
    }
  };
  const subs = new Map<string, () => void>();
  const watch = () => {
    for (const t of topics.values()) if (!subs.has(t.id)) subs.set(t.id, t.subscribe(() => mark(t.id)));
  };
  watch();
  registryListeners.add(watch);
  const offRequest = bus.on("mirror-request", (msg) => {
    for (const id of msg.topics ?? [...topics.keys()]) if (topics.has(id)) publish(id);
  });
  const offCommand = bus.on("command", (msg) => {
    if (msg.to !== bus.id) return;
    const c = commands.get(msg.name);
    if (!c) {
      console.warn(`[windows] no command "${msg.name}" for another window`);
      return;
    }
    try {
      c.run(msg.args, msg.from);
    } catch (err) {
      console.warn(`[windows] the command "${msg.name}" failed`, err);
    }
  });
  return () => {
    registryListeners.delete(watch);
    for (const off of subs.values()) off();
    subs.clear();
    offRequest();
    offCommand();
  };
}

/**
 * A pop-out window's side: apply the snapshots the main window publishes (an
 * older one than already applied is skipped), and ask for all of them now and
 * whenever a main window appears (it started, or came back). Returns the uninstall.
 */
export function followMirror(bus: WindowBus): () => void {
  const applied = new Map<string, { from: WindowId; seq: number }>();
  const offMirror = bus.on("mirror", (msg) => {
    const t = topics.get(msg.topic);
    if (!t) return;
    const had = applied.get(msg.topic);
    if (had && had.from === msg.from && had.seq >= msg.seq) return;
    applied.set(msg.topic, { from: msg.from, seq: msg.seq });
    try {
      t.apply(msg.data);
    } catch (err) {
      console.warn(`[windows] applying the mirror topic "${msg.topic}" failed`, err);
    }
  });
  let mainSeen = false;
  const request = () => bus.post({ type: "mirror-request" });
  const offWindows = bus.subscribe(() => {
    const live = bus.windows().some((w) => w.main);
    if (live && !mainSeen) {
      // A main window that is new to us numbers its snapshots from 1 again.
      applied.clear();
      request();
    }
    mainSeen = live;
  });
  request();
  return () => {
    offMirror();
    offWindows();
  };
}

/** Ask the main window to run `name` with `args` (a no-op without one). */
export function sendMirrorCommand(bus: WindowBus, name: string, args: unknown = null): void {
  bus.post({ type: "command", to: MAIN_WINDOW_ID, name, args });
}
