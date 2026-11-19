// Editing the keyboard shortcuts: the rules and the model behind the shortcuts dialog.
//
// What a key IS lives in `./shortcuts` (the registry with the user's overrides applied in one place);
// this module is what an editor needs on top: which chords the browser or the OS will not deliver
// (refused), turning a key press into a chord, who already holds a chord (a conflict), and the
// rows of commands with their effective and default chords. The overrides are saved in the layout
// prefs (`LayoutPrefs.keyOverrides`, not per shell).
//
// Chord SEQUENCES (two chords in a row) are not supported: the registry matches one key event
// against one chord, and `CommandSpec.chordLabel` is display-only text, so there is nothing to capture.

import { listCommands, type RegisteredCommand } from "./commands";
import { layoutStore } from "./layoutStore";
import type { PanelId } from "./panel";
import { CORE_SHORTCUTS } from "./coreShortcutTable";
import { isPanelOpen, toggle } from "./reveal";
import {
  defaultCoreShortcutKeys,
  listDefaultShortcuts,
  listShortcuts,
  parseShortcut,
  shortcutOverrides,
  type RegisteredShortcut,
  type ShortcutKeys,
} from "./shortcuts";

const MODIFIER_KEYS = new Set(["control", "shift", "alt", "meta", "altgraph", "os", "capslock", "dead"]);

/** Chords the browser or the OS keeps for itself (tabs, windows, reload, developer tools): never delivered to the page, or not ours to take. */
const UNSAFE = new Set(
  [
    "ctrl+w", "ctrl+t", "ctrl+n", "ctrl+shift+n", "ctrl+shift+t", "ctrl+shift+w", "ctrl+tab", "ctrl+shift+tab",
    "ctrl+pageup", "ctrl+pagedown", "ctrl+q", "ctrl+l", "ctrl+r", "ctrl+shift+r", "ctrl+f5", "f5", "f11", "f12",
    "ctrl+shift+i", "ctrl+shift+j", "ctrl+shift+c", "ctrl+shift+delete", "ctrl+u", "ctrl+d", "ctrl+h", "ctrl+j",
    "alt+f4", "alt+tab", "alt+arrowleft", "alt+arrowright", "alt+home", "ctrl+1", "ctrl+2", "ctrl+3", "ctrl+4",
    "ctrl+5", "ctrl+6", "ctrl+7", "ctrl+8", "ctrl+9", "ctrl+0", "ctrl+plus", "ctrl+minus", "ctrl+=", "ctrl+-",
  ].map((c) => c.toLowerCase()),
);

/** Keys that would break the page itself if a command took them. */
const NEVER = new Set(["escape", "tab", "enter", " ", "space", "backspace", "delete", "shift", "control", "alt", "meta"]);

/** A chord in the registry's spelling, normalised for comparison (`ctrl+shift+k`). */
export function normaliseChord(keys: ShortcutKeys): string {
  const c = parseShortcut(keys);
  if (!c) return "";
  return [c.ctrl ? "ctrl" : "", c.alt ? "alt" : "", c.shift ? "shift" : "", c.key].filter(Boolean).join("+");
}

/** Why `keys` may not be assigned, or null when it may. */
export function refuseChord(keys: ShortcutKeys): string | null {
  const c = parseShortcut(keys);
  if (!c || !c.key) return "That is not a key combination.";
  if (MODIFIER_KEYS.has(c.key)) return "A modifier key alone cannot be a shortcut.";
  const norm = normaliseChord(keys);
  if (!c.ctrl && !c.alt && NEVER.has(c.key)) return `${keys} is needed for the page itself (closing dialogs, moving focus, typing) and cannot be a shortcut.`;
  if (UNSAFE.has(norm)) return `${keys} is reserved by the browser or the operating system, which does not hand it to the page.`;
  return null;
}

/**
 * The chord a key press stands for, in the registry's spelling: modifiers then the key
 * (`Ctrl+Shift+K`). Null while only modifiers are held.
 */
export function chordFromEvent(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }): ShortcutKeys | null {
  if (MODIFIER_KEYS.has(e.key.toLowerCase())) return null;
  const key = e.key === " " ? "Space" : e.key === "+" ? "Plus" : e.key.length === 1 ? e.key.toUpperCase() : e.key;
  return [e.ctrlKey || e.metaKey ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", key].filter(Boolean).join("+");
}

/** The shortcut that holds `keys` now (live ones only; a widget's local keys do not count), other than `exceptId`. */
export function chordHolder(keys: ShortcutKeys, exceptId?: string, list: readonly RegisteredShortcut[] = listShortcuts()): RegisteredShortcut | null {
  const want = normaliseChord(keys);
  return list.find((s) => !s.local && s.id !== exceptId && normaliseChord(s.keys) === want) ?? null;
}

/** The id the user's key for command `c` is kept under: the core shortcut it shows, its own registered one, or `cmd:<id>`. */
export function shortcutIdOf(c: Pick<RegisteredCommand, "id" | "shortcut">, base: readonly RegisteredShortcut[] = listDefaultShortcuts()): string {
  if (c.shortcut && CORE_SHORTCUTS.some((d) => d.id === c.shortcut)) return c.shortcut;
  if (base.some((s) => s.id === `cmd:${c.id}`)) return `cmd:${c.id}`;
  if (base.some((s) => s.id === c.id)) return c.id;
  return `cmd:${c.id}`;
}

/** One line of the editor. */
export interface KeyRow {
  /** The key the override is stored under. */
  shortcutId: string;
  /** The command it runs, when it is one. */
  commandId: string | null;
  label: string;
  group: string;
  keywords: readonly string[];
  /** The chord in force now (the user's, else the default); undefined: none. */
  keys: ShortcutKeys | undefined;
  /** The chord with no override. */
  defaultKeys: ShortcutKeys | undefined;
  /** The user changed it (or unbound it). */
  overridden: boolean;
  /** A widget's own key: listed, not editable. */
  locked: boolean;
  /** Shown in place of `keys`: a run of keys ("1–9"). */
  keysLabel?: string;
  /**
   * More chords that run the same command, shown beside the one in force: a shell's alias (the
   * command's own `keys` where its `shortcut` names a core key, a second chord for the palette say).
   */
  extraKeys?: readonly ShortcutKeys[];
}

type Modifier = "ctrl" | "alt" | "shift";
const MODIFIER_WORDS: Readonly<Record<string, Modifier>> = { ctrl: "ctrl", control: "ctrl", cmd: "ctrl", "⌘": "ctrl", meta: "ctrl", alt: "alt", option: "alt", shift: "shift" };
/** Key names a query may spell out, as the registry spells the key. */
const KEY_WORDS: Readonly<Record<string, string>> = {
  esc: "escape", escape: "escape", enter: "enter", return: "enter", tab: "tab", space: " ", backspace: "backspace",
  delete: "delete", del: "delete", home: "home", end: "end", pageup: "pageup", pagedown: "pagedown",
  up: "arrowup", down: "arrowdown", left: "arrowleft", right: "arrowright",
  arrowup: "arrowup", arrowdown: "arrowdown", arrowleft: "arrowleft", arrowright: "arrowright",
  plus: "+", minus: "-",
};

/**
 * A search for keys rather than words: modifiers alone ("alt", "ctrl shift"), one key ("g", "5",
 * "`"), or modifiers and a key ("ctrl+k", "shift h"). `wordToo`: the query is also a word a
 * label may hold ("delete", "escape"), so a label match counts as well. Null: not a key search.
 */
export function parseKeyQuery(query: string): { mods: ReadonlySet<Modifier>; key?: string; wordToo: boolean } | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  // A lone "+" or "-" is the key itself, not a separator.
  const tokens = q.length === 1 ? [q] : q.split(/[\s+]+/).filter(Boolean);
  if (!tokens.length) return null;
  const mods = new Set<Modifier>();
  let key: string | undefined;
  for (const [i, t] of tokens.entries()) {
    const mod = MODIFIER_WORDS[t];
    if (mod) {
      if (key !== undefined) return null;
      mods.add(mod);
      continue;
    }
    if (i !== tokens.length - 1) return null;
    const named = /^f([1-9]|1[0-2])$/.test(t) ? t : KEY_WORDS[t];
    if (t.length === 1) key = t;
    else if (named) key = named;
    else return null;
  }
  return { mods, ...(key !== undefined ? { key } : {}), wordToo: tokens.length === 1 && key !== undefined && key.length > 1 };
}

/**
 * How well `query`, read as keys (`parseKeyQuery`), names one of `row`'s chords: 0 the chord
 * itself (the same modifiers and key), 1 a chord with those modifiers and more, or on that key
 * with modifiers ("g" finds Shift+G too); null none, or not a key search.
 */
export function chordQueryRank(query: string, row: Pick<KeyRow, "keys" | "keysLabel" | "extraKeys">): number | null {
  const want = parseKeyQuery(query);
  if (!want) return null;
  const chords = [row.keys, ...(row.extraKeys ?? [])]
    .map((k) => (k ? parseShortcut(k) : null))
    .filter((c): c is NonNullable<ReturnType<typeof parseShortcut>> => !!c)
    .map((c) => ({ key: c.key, mods: new Set<Modifier>([...(c.ctrl ? ["ctrl" as const] : []), ...(c.alt ? ["alt" as const] : []), ...(c.shift ? ["shift" as const] : [])]) }));
  // A run of keys ("1–9"): every key in it, bare.
  const run = row.keysLabel ? /^(.)–(.)$/.exec(row.keysLabel) : null;
  if (run && want.key?.length === 1 && want.key >= run[1] && want.key <= run[2]) chords.push({ key: want.key, mods: new Set() });
  let best: number | null = null;
  for (const c of chords) {
    if (want.key !== undefined && c.key !== want.key) continue;
    if (![...want.mods].every((m) => c.mods.has(m))) continue;
    const exact = c.mods.size === want.mods.size;
    // Modifiers alone list every chord holding them; none is "the" chord.
    const rank = want.key !== undefined && exact ? 0 : 1;
    best = best === null ? rank : Math.min(best, rank);
  }
  return best;
}

/** Every command (and every core key with no command of its own) as editor rows, with their effective and default chords. */
export function keyRows(
  toggleFn: (id: PanelId) => void = (id) => void toggle(id),
  isOpen: (id: PanelId) => boolean = isPanelOpen,
): KeyRow[] {
  const base = listDefaultShortcuts();
  const overrides = shortcutOverrides();
  const defaults = new Map(base.map((s) => [s.id, s]));
  const rows = new Map<string, KeyRow>();
  const keysOf = (sid: string): ShortcutKeys | undefined => {
    const o = overrides[sid];
    if (o === undefined) return defaults.get(sid)?.keys;
    return o === null || !parseShortcut(o) ? undefined : o;
  };
  const dflt = (sid: string, c?: RegisteredCommand): ShortcutKeys | undefined =>
    defaults.get(sid)?.keys ?? (c?.shortcut ? defaultCoreShortcutKeys(c.shortcut) : undefined) ?? c?.keys;
  // A command's own chord (`cmd:<id>`) where the row is the core key it names: one row, the alias beside it.
  const folded = new Set<string>();
  for (const c of listCommands({ toggle: toggleFn, isOpen })) {
    const sid = shortcutIdOf(c, base);
    if (rows.has(sid)) continue;
    const alias = `cmd:${c.id}`;
    const hasAlias = alias !== sid && defaults.has(alias);
    if (hasAlias) folded.add(alias);
    const keys = defaults.get(sid)?.local ? defaults.get(sid)?.keys : keysOf(sid);
    const aliasKeys = hasAlias ? keysOf(alias) : undefined;
    rows.set(sid, {
      shortcutId: sid,
      commandId: c.id,
      label: c.label,
      group: c.group ?? "Commands",
      keywords: c.keywords ?? [],
      keys,
      defaultKeys: dflt(sid, c),
      overridden: !defaults.get(sid)?.local && sid in overrides,
      locked: !!defaults.get(sid)?.local,
      ...(defaults.get(sid)?.keysLabel ? { keysLabel: defaults.get(sid)!.keysLabel } : {}),
      ...(aliasKeys && normaliseChord(aliasKeys) !== normaliseChord(keys ?? "") ? { extraKeys: [aliasKeys] } : {}),
    });
  }
  // Keys that have no command: core's own table (the tree walk is also a command, the rest are keys only), shell and plugin shortcuts.
  for (const s of base) {
    if (rows.has(s.id) || folded.has(s.id)) continue;
    rows.set(s.id, {
      shortcutId: s.id,
      commandId: null,
      label: s.label,
      group: s.group ?? "Shell and plugins",
      keywords: [],
      keys: s.local ? s.keys : keysOf(s.id),
      defaultKeys: s.keys,
      overridden: !s.local && s.id in overrides,
      locked: !!s.local,
      ...(s.keysLabel ? { keysLabel: s.keysLabel } : {}),
    });
  }
  return [...rows.values()];
}

/** Bind `shortcutId` to `keys` (the caller has dealt with a conflict), leave it unbound (`null`), or back to its default (`undefined`). */
export function setKeyBinding(shortcutId: string, keys: ShortcutKeys | null | undefined): void {
  layoutStore.getState().setKeyOverride(shortcutId, keys);
}

/** Every key back to its default. */
export function resetAllKeyBindings(): void {
  layoutStore.getState().resetKeyOverrides();
}

/**
 * Assign `keys` to `shortcutId`: refused with a reason for an unsafe chord, reported as a conflict (the
 * holder) unless `replace`, in which case the holder is left unbound. The editor's one entry point.
 */
export function assignKeys(
  shortcutId: string,
  keys: ShortcutKeys,
  opts: { replace?: boolean } = {},
): { ok: true } | { ok: false; reason: string; conflict?: RegisteredShortcut } {
  const refusal = refuseChord(keys);
  if (refusal) return { ok: false, reason: refusal };
  const holder = chordHolder(keys, shortcutId);
  if (holder && !opts.replace) return { ok: false, reason: `${keys} is already used by "${holder.label}".`, conflict: holder };
  if (holder) setKeyBinding(holder.id, null);
  setKeyBinding(shortcutId, keys);
  return { ok: true };
}
