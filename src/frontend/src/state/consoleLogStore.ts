// What the page has logged, for the Console panel: the last `CONSOLE_LOG_MAX` console calls, uncaught
// errors and unhandled rejections (see utils/consoleCapture, which fills it), plus the panel's flag.
//
// Entries arrive in batches (`appendConsoleEntries`), so a burst of logging is one store update and a
// closed panel costs nothing but the buffer.

import { create } from "zustand";

export type ConsoleLevel = "debug" | "log" | "info" | "warn" | "error";

export const CONSOLE_LEVELS: readonly ConsoleLevel[] = ["debug", "log", "info", "warn", "error"];

export interface ConsoleEntry {
  id: number;
  /** ms since the epoch. */
  t: number;
  level: ConsoleLevel;
  text: string;
}

/** How many entries are kept; older ones fall off the front. */
export const CONSOLE_LOG_MAX = 2000;

type ConsoleLogState = {
  isVisible: boolean;
  setIsVisible: (v: boolean) => void;
  toggleVisible: () => void;

  entries: ConsoleEntry[];
  clear: () => void;
};

export const useConsoleLogStore = create<ConsoleLogState>((set, get) => ({
  isVisible: false,
  setIsVisible: (isVisible) => set({ isVisible }),
  toggleVisible: () => set({ isVisible: !get().isVisible }),

  entries: [],
  clear: () => set({ entries: [] }),
}));

/** Add entries, keeping the last `CONSOLE_LOG_MAX`. */
export function appendConsoleEntries(batch: readonly ConsoleEntry[]): void {
  if (batch.length === 0) return;
  useConsoleLogStore.setState((s) => {
    const all = s.entries.concat(batch);
    return { entries: all.length > CONSOLE_LOG_MAX ? all.slice(all.length - CONSOLE_LOG_MAX) : all };
  });
}
