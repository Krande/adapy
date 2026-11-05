// Keyboard shortcuts a shell or a plugin adds, and the panel shortcuts of
// panels that are not core's (`PanelSpec.shortcut`), dispatched by one listener
// the layout mounts (`./ShortcutKeys`) and listed in the shortcuts dialog.
//
// Core's own keys stay where they are handled (the viewer's key handler, the
// trees, the cell builder) and are RESERVED here: a registration that names one
// is refused with a warning, so nothing silently takes a key users know.
//
// Dependency-free, so the facade root can export it.

import type { PanelId } from "./panel";
import { listPanels } from "./registry";

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
  run(): void;
  /** Live only while this holds. */
  when?(): boolean;
}

export interface RegisteredShortcut extends ShortcutSpec {
  owner: string;
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

/** The chords core handles itself (the shortcuts dialog's global, gallery and cell builder lists). */
export const RESERVED_SHORTCUTS: readonly ShortcutKeys[] = [
  ..."HIGUFAQWPRTC".split("").map((k) => `Shift+${k}`),
  "Shift+Enter",
  "Shift+ArrowUp",
  "Shift+ArrowDown",
  "Shift+ArrowLeft",
  "Shift+ArrowRight",
  "Ctrl+C",
  "Ctrl+Z",
  "Ctrl+Y",
  "Ctrl+Shift+Z",
  "F6",
  "Shift+F6",
  "Escape",
];
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

/** Registered shortcuts, then non-core panels' `shortcut`s (as "Show or hide <title>"). */
export function listShortcuts(toggle?: (id: PanelId) => void): RegisteredShortcut[] {
  const out = [..._shortcuts.values()];
  const taken = new Set(out.map((s) => chordKey(parseShortcut(s.keys)!)));
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
}

/** The shortcut a key press asks for, if any (and live). */
export function matchShortcut(e: ShortcutKeyEvent, list: readonly RegisteredShortcut[]): RegisteredShortcut | null {
  const pressed = chordKey({ key: e.key.toLowerCase(), shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey, alt: e.altKey });
  for (const s of list) {
    if (chordKey(parseShortcut(s.keys)!) !== pressed) continue;
    if (s.when && !s.when()) continue;
    return s;
  }
  return null;
}

/** Clear the registry -- unit tests only. */
export function resetShortcuts(): void {
  _shortcuts.clear();
}
