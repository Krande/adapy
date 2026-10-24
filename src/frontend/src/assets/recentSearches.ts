// The Sources tab's recent searches: the last few terms a person searched the tree for, offered
// in a dropdown when the search box is focused.
//
// Per viewer and per browser (localStorage): a convenience, not shared state. Every read and
// write is guarded, so a private window or blocked storage just means no history.

import { isSearchTerm } from "@/assets/rowFacts";

export const RECENT_SEARCHES_MAX = 5;
export const RECENT_SEARCHES_KEY = "ada.sources.recentSearches";

/** `term` added to the front of `list`: trimmed, once (a repeat in any case moves to the front,
 *  spelled as typed now), at most `max` kept. A term too short to search is not remembered. */
export function withRecent(list: readonly string[], term: string, max = RECENT_SEARCHES_MAX): string[] {
  const t = term.trim();
  if (!isSearchTerm(t)) return [...list];
  const folded = t.toLowerCase();
  return [t, ...list.filter((x) => x.toLowerCase() !== folded)].slice(0, max);
}

/** What the dropdown offers for what is typed: every recent term while the box is empty or holds
 *  one of them; otherwise those containing the text (any case), minus an exact repeat of it. */
export function recentFor(list: readonly string[], typed: string): string[] {
  const t = typed.trim().toLowerCase();
  if (!t || list.some((x) => x.toLowerCase() === t)) return [...list];
  return list.filter((x) => x.toLowerCase().includes(t));
}

export function loadRecent(storage: Pick<Storage, "getItem"> | undefined = safeStorage()): string[] {
  try {
    const raw = storage?.getItem(RECENT_SEARCHES_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string" && !!x.trim()).slice(0, RECENT_SEARCHES_MAX)
      : [];
  } catch {
    return [];
  }
}

export function saveRecent(list: readonly string[], storage: Pick<Storage, "setItem"> | undefined = safeStorage()): void {
  try {
    storage?.setItem(RECENT_SEARCHES_KEY, JSON.stringify(list.slice(0, RECENT_SEARCHES_MAX)));
  } catch {
    // No storage (private window, blocked site data): the history just is not kept.
  }
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window !== "undefined" ? window.localStorage : undefined;
  } catch {
    return undefined;
  }
}
