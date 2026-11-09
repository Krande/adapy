// The model of a composed shell's status bar (`./StatusBar`): which items core offers, what an
// item is, and the look of the shell's bars. Dependency-free (types and constants), so the facade
// root can export it and a shell's spec can name items without loading the bar.

import type React from "react";

/** Core's status items, by id: what a shell lists in `UiShellChrome.statusItems`. */
export const CORE_STATUS_ITEM_IDS = ["connection", "models", "selection", "mode", "undo", "compile", "version"] as const;
export type CoreStatusItemId = (typeof CORE_STATUS_ITEM_IDS)[number];

/** One entry of the status bar: plain text, or content of the shell's own. */
export interface StatusBarItem {
  /** Unique within the bar. */
  id: string;
  /** Plain text (drawn as `StatusItem`). */
  text?: string;
  /** Tooltip of the text. */
  title?: string;
  /** Draw the text in the muted colour. */
  muted?: boolean;
  /** Content of the shell's own, in place of `text`: an element, which may use hooks (draw it inside `StatusItem` to match core's). */
  content?: React.ReactNode;
  /** Drawn at the right end, after the spare room. */
  end?: boolean;
}

/** What `UiShellChrome.statusItems` lists: core's items by id and the shell's own. */
export type StatusItemSpec = CoreStatusItemId | StatusBarItem;

/** Is `id` one of core's status items? */
export const isCoreStatusItem = (id: string): id is CoreStatusItemId => (CORE_STATUS_ITEM_IDS as readonly string[]).includes(id);

/**
 * The look of a shell's bars -- the menu bar, the status bar, the page header: core's window tokens, the
 * classic colours where unset. A shell that sets those tokens (`themeTokens`) restyles them all.
 */
export const SHELL_BAR =
  "flex shrink-0 items-center gap-2 border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-header-bg,#1f2937)] " +
  "text-[var(--ada-window-header-text,#f3f4f6)]";
