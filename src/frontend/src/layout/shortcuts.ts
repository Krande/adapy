// Keyboard shortcuts: core's own (`./coreShortcutTable`), the ones a shell or a
// plugin adds, and the panel shortcuts of panels that are not core's
// (`PanelSpec.shortcut`). One registry, so the keys are bound, listed in the
// shortcuts dialog (`listShortcuts`), shown beside a command (`coreShortcutKeys`)
// and checked for conflicts from the same entries. The listener that dispatches
// them is `./shortcutDispatch`.
//
// Core's entries are always present (they are data); a viewer gives them their
// handlers while it is mounted (`bindCoreShortcuts`). A registration that names a
// chord core handles is refused with a warning, so nothing silently takes a key
// users know.
//
// Dependency-free, so the facade root can export it.

import type { PanelId } from "./panel";
import { listPanels } from "./registry";
import { CORE_SHORTCUTS } from "./coreShortcutTable";

/** A chord in the shared vocabulary: modifiers then the key, `+`-joined (`"Shift+K"`, `"Ctrl+Alt+M"`). */
export type ShortcutKeys = string;

export interface ShortcutSpec {
  /** Unique; an owner's later registration under the same id replaces its earlier one. */
  id: string;
  keys: ShortcutKeys;
  /** What it does, for the shortcuts dialog. */
  label: string;
  /** The dialog's group. Default: "Shell and plugins". */
  group?: string;
  /** Return `false` when the press was not handled after all: its default is then left alone. */
  run(): void | boolean;
  /** Live only while this holds (given the key event when there is one). */
  when?(e?: ShortcutKeyEvent): boolean;
  /** Also fire on auto-repeat (a held key). Default: only the first press. */
  repeat?: boolean;
  /** Leave the browser's default for the key alone once handled. Default: it is prevented. */
  keepDefault?: boolean;
}

export interface RegisteredShortcut extends ShortcutSpec {
  owner: string;
  /** A key a widget handles itself (core's local keys): listed, never dispatched. */
  local?: boolean;
  /** Listed only while this holds (core's panel keys). */
  requires?: "customisable-panels";
}

interface Chord {
  key: string;
  shift: boolean;
  ctrl: boolean;
  alt: boolean;
}

/** `"Shift+K"` as a chord; `Ctrl` also matches ⌘ on macOS. Null for a chord with no key. */
export function parseShortcut(keys: ShortcutKeys): Chord | null {
  const parts = keys.split("+").map((p) => p.trim());
  const key = parts.pop()?.toLowerCase();
  if (!key) return null;
  const mods = new Set(parts.filter(Boolean).map((p) => p.toLowerCase()));
  return { key, shift: mods.has("shift"), ctrl: mods.has("ctrl") || mods.has("cmd") || mods.has("meta"), alt: mods.has("alt") };
}

const chordKey = (c: Chord) => `${c.ctrl ? "c" : ""}${c.alt ? "a" : ""}${c.shift ? "s" : ""}+${c.key}`;

/** What a viewer does for a dispatched core shortcut. */
export interface CoreShortcutHandler {
  run(): void | boolean;
  when?(e?: ShortcutKeyEvent): boolean;
}

/** Core's entries as the registry holds them: always listed, live once a viewer has bound handlers. */
const _core = new Map<string, RegisteredShortcut>();
const _handlers = new Map<string, CoreShortcutHandler[]>();

for (const def of CORE_SHORTCUTS) {
  const handlers = () => _handlers.get(def.id) ?? [];
  _core.set(def.id, {
    id: def.id,
    keys: def.keys,
    label: def.label,
    group: def.group,
    owner: "core",
    local: def.local,
    requires: def.requires,
    repeat: true,
    keepDefault: def.keepDefault,
    // Every viewer bound (a page may hold several, each scoped) gets its say.
    when: (e) => !def.local && handlers().some((h) => !h.when || h.when(e)),
    run: () => {
      let handled = false;
      for (const h of handlers()) {
        if (h.when && !h.when()) continue;
        if (h.run() !== false) handled = true;
      }
      return handled;
    },
  });
}

/** Give core's dispatched shortcuts their handlers (by id); returns the undo. Called by a mounted viewer. */
export function bindCoreShortcuts(handlers: Readonly<Record<string, CoreShortcutHandler>>): () => void {
  const bound: [string, CoreShortcutHandler][] = [];
  for (const [id, h] of Object.entries(handlers)) {
    const def = _core.get(id);
    if (!def || def.local) {
      console.warn(`[layout] "${id}" is not a dispatched core shortcut; ignored`);
      continue;
    }
    _handlers.set(id, [...(_handlers.get(id) ?? []), h]);
    bound.push([id, h]);
  }
  return () => {
    for (const [id, h] of bound) {
      const rest = (_handlers.get(id) ?? []).filter((x) => x !== h);
      if (rest.length) _handlers.set(id, rest);
      else _handlers.delete(id);
    }
  };
}

/** The chord of core shortcut `id` (what a command with `shortcut: id` shows), if there is one. */
export function coreShortcutKeys(id: string): ShortcutKeys | undefined {
  return _core.get(id)?.keys;
}

/** The chords plugins are refused: core's dispatched keys, and its local keys that nothing else may take. */
const reservedEntries = CORE_SHORTCUTS.filter((d) => d.reserves ?? !d.local);
export const RESERVED_SHORTCUTS: readonly ShortcutKeys[] = [...new Set(reservedEntries.map((d) => d.keys))];
const reserved = new Set(RESERVED_SHORTCUTS.map((k) => chordKey(parseShortcut(k)!)));

const _shortcuts = new Map<string, RegisteredShortcut>();

/** Register a shortcut. Refused (false, with a warning) for a reserved or already taken chord. */
export function registerShortcut(spec: ShortcutSpec, owner: string): boolean {
  const chord = parseShortcut(spec.keys);
  if (!spec.id || !chord) {
    console.warn(`[layout] shortcut "${spec.id}" has no id or no key; ignored`);
    return false;
  }
  const ck = chordKey(chord);
  if (reserved.has(ck)) {
    console.warn(`[layout] shortcut "${spec.id}": ${spec.keys} is one of core's own keys; ignored`);
    return false;
  }
  for (const s of _shortcuts.values()) {
    if (s.id !== spec.id && chordKey(parseShortcut(s.keys)!) === ck) {
      console.warn(`[layout] shortcut "${spec.id}": ${spec.keys} is taken by "${s.id}"; ignored`);
      return false;
    }
  }
  _shortcuts.set(spec.id, { ...spec, owner });
  return true;
}

export function unregisterShortcut(id: string): void {
  _shortcuts.delete(id);
}

/** The registered shortcuts of `owner` (not core's own table, not panels'). */
export function shortcutsOwnedBy(owner: string): RegisteredShortcut[] {
  return [..._shortcuts.values()].filter((s) => s.owner === owner);
}

/** Core's shortcuts, then registered ones, then non-core panels' `shortcut`s (as "Show or hide <title>"). */
export function listShortcuts(toggle?: (id: PanelId) => void): RegisteredShortcut[] {
  const out = [..._core.values(), ..._shortcuts.values()];
  const taken = new Set(out.filter((s) => !s.local).map((s) => chordKey(parseShortcut(s.keys)!)));
  for (const p of listPanels()) {
    if (p.owner === "core" || !p.shortcut) continue;
    const chord = parseShortcut(p.shortcut);
    if (!chord || reserved.has(chordKey(chord)) || taken.has(chordKey(chord))) continue;
    taken.add(chordKey(chord));
    out.push({
      id: `panel:${p.id}`,
      keys: p.shortcut,
      label: `Show or hide ${p.title}`,
      group: "Panels",
      owner: p.owner,
      run: () => toggle?.(p.id),
    });
  }
  return out;
}

export interface ShortcutKeyEvent {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  target?: EventTarget | null;
}

/** The shortcut a key press asks for, if any (and live). Local entries are never matched. */
export function matchShortcut(e: ShortcutKeyEvent, list: readonly RegisteredShortcut[]): RegisteredShortcut | null {
  const pressed = chordKey({ key: e.key.toLowerCase(), shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey, alt: e.altKey });
  for (const s of list) {
    if (s.local || chordKey(parseShortcut(s.keys)!) !== pressed) continue;
    if (s.when && !s.when(e)) continue;
    return s;
  }
  return null;
}

/** Clear the registered shortcuts (not core's own) and core's bound handlers -- unit tests only. */
export function resetShortcuts(): void {
  _shortcuts.clear();
  _handlers.clear();
}