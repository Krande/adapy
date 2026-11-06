// The tool rail: a strip of icon buttons down the left of a composed shell's
// layout, each a command (`./commands`), that the user may rearrange. The pure
// half -- the shell's configuration, the user's arrangement and its upkeep
// across releases, the edits the customise dialog makes -- so it is tested
// under plain `node --test`. The component and the dialog are `./ToolRail`.
//
// A shell gives the tools it starts with (`UiShellChrome.rail: {tools}`); the
// user's own list is kept per shell in the layout prefs (`LayoutPrefs.rail`),
// so it goes where the arrangements go (the server, for a signed-in user).
//
// The saved list is the user's WHOLE list, with the shell's list as it was when
// they saved it (`base`). A tool the shell adds in a later release is in neither,
// so it is put in, next to where the shell has it; a tool the user removed is in
// `base` and stays out. A tool whose command no longer exists is left out when
// the rail is drawn (and stays in the saved list, in case it comes back).
//
// Dependency-free.

/** A line between groups of tools, in a rail's list. */
export const RAIL_SEPARATOR = "|";

/** A shell's tool rail (`UiShellChrome.rail` as data). */
export interface ToolRailConfig {
  /** Command ids, top to bottom; `RAIL_SEPARATOR` draws a line between groups. */
  tools: readonly string[];
  /** The user may add, remove and reorder tools (the customise dialog, the rail's menu). Default true. */
  customisable?: boolean;
}

/** The user's rail in one shell (`LayoutPrefs.rail[shellId]`). */
export interface RailPrefs {
  /** Their list, top to bottom, separators included. */
  tools: string[];
  /** The shell's list when they saved theirs: what tells a tool added since from one they removed. */
  base: string[];
}

export const isRailSeparator = (id: string): boolean => id === RAIL_SEPARATOR;

/** The rail of `chrome.rail` as data; null when the shell draws a rail component of its own (or none). */
export function railConfig(rail: unknown): ToolRailConfig | null {
  if (!rail || typeof rail !== "object") return null;
  const tools = (rail as { tools?: unknown }).tools;
  if (!Array.isArray(tools)) return null;
  return {
    tools: tools.filter((t): t is string => typeof t === "string" && t.length > 0),
    customisable: (rail as { customisable?: unknown }).customisable !== false,
  };
}

/** Saved rail prefs made safe to use; null for anything that is not a rail. */
export function migrateRailPrefs(raw: unknown): RailPrefs | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { tools?: unknown; base?: unknown };
  if (!Array.isArray(r.tools)) return null;
  const strings = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.length > 0) : []);
  return { tools: strings(r.tools), base: strings(r.base) };
}

/**
 * Separators tidied (none at either end, never two in a row), a tool listed
 * twice kept once, and, given `offered`, the tools nothing offers left out.
 */
export function tidyRail(ids: readonly string[], offered?: (id: string) => boolean): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    if (isRailSeparator(id)) {
      if (out.length && !isRailSeparator(out[out.length - 1])) out.push(id);
      continue;
    }
    if (seen.has(id) || (offered && !offered(id))) continue;
    seen.add(id);
    out.push(id);
  }
  while (out.length && isRailSeparator(out[out.length - 1])) out.pop();
  return out;
}

/**
 * The rail a user sees in a shell whose list is `defaults`: theirs (`saved`),
 * with the tools the shell added since they saved put in next to the tool
 * before them in the shell's list; the shell's own when they have none.
 */
export function railTools(defaults: readonly string[], saved: RailPrefs | null | undefined): string[] {
  if (!saved) return tidyRail(defaults);
  const out = [...saved.tools];
  const base = new Set(saved.base);
  defaults.forEach((id, i) => {
    if (isRailSeparator(id) || base.has(id) || out.includes(id)) return;
    // After the nearest tool before it in the shell's list that the user has, else at the top.
    let at = 0;
    for (let j = i - 1; j >= 0; j--) {
      const k = out.indexOf(defaults[j]);
      if (!isRailSeparator(defaults[j]) && k >= 0) {
        at = k + 1;
        break;
      }
    }
    out.splice(at, 0, id);
  });
  return dedupeRail(out);
}

/**
 * A tool listed twice kept once (the first); separators as they are, so the
 * customise dialog shows where the user put them, even at an end or two in a
 * row (the rail as drawn leaves those out, `tidyRail`).
 */
export function dedupeRail(ids: readonly string[]): string[] {
  const seen = new Set<string>();
  return ids.filter((id) => {
    if (isRailSeparator(id)) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

/** Whether the user's list is the shell's own (nothing to reset). */
export function railIsDefault(defaults: readonly string[], saved: RailPrefs | null | undefined): boolean {
  const a = railTools(defaults, saved);
  const b = tidyRail(defaults);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** The list with the entry at `from` moved to `to` (indices in the list; clamped). */
export function moveRailTool(list: readonly string[], from: number, to: number): string[] {
  if (from < 0 || from >= list.length) return [...list];
  const out = [...list];
  const [it] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(out.length, to)), 0, it);
  return out;
}

/** The list with `id` added at `at` (default the end); a tool already on it is not added twice. */
export function addRailTool(list: readonly string[], id: string, at: number = list.length): string[] {
  if (!id || (!isRailSeparator(id) && list.includes(id))) return [...list];
  const out = [...list];
  out.splice(Math.max(0, Math.min(out.length, at)), 0, id);
  return out;
}

/** The list without the entry at `at`. */
export function removeRailTool(list: readonly string[], at: number): string[] {
  return list.filter((_, i) => i !== at);
}
