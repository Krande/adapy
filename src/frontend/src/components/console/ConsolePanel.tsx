// The Console panel: what the page has logged (console calls, uncaught errors, unhandled
// rejections), captured by utils/consoleCapture into state/consoleLogStore. Find it with Ctrl+K
// ("Console") like every panel.

import React, { useEffect, useMemo, useRef, useState } from "react";

import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import { CONSOLE_LEVELS, type ConsoleEntry, type ConsoleLevel, useConsoleLogStore } from "@/state/consoleLogStore";

const LEVEL_STYLE: Record<ConsoleLevel, string> = {
  debug: "text-gray-500",
  log: "text-gray-200",
  info: "text-sky-300",
  warn: "text-yellow-300",
  error: "text-red-300",
};

/** Within this many pixels of the bottom, new entries keep the view scrolled to the end. */
const STICK_PX = 24;

const time = (t: number) => new Date(t).toLocaleTimeString(undefined, { hour12: false });

/** Entries matching the shown levels and the filter text (case-insensitive). */
export function filterConsoleEntries(
  entries: readonly ConsoleEntry[],
  shown: ReadonlySet<ConsoleLevel>,
  query: string,
): ConsoleEntry[] {
  const q = query.trim().toLowerCase();
  return entries.filter((e) => shown.has(e.level) && (!q || e.text.toLowerCase().includes(q)));
}

/** The panel's content: level toggles, filter, clear / copy, and the log. */
export const ConsoleBody: React.FC = () => {
  const entries = useConsoleLogStore((s) => s.entries);
  const clear = useConsoleLogStore((s) => s.clear);
  const [shown, setShown] = useState<ReadonlySet<ConsoleLevel>>(new Set(["log", "info", "warn", "error"]));
  const [query, setQuery] = useState("");
  const visible = useMemo(() => filterConsoleEntries(entries, shown, query), [entries, shown, query]);
  const counts = useMemo(() => {
    const c: Record<ConsoleLevel, number> = { debug: 0, log: 0, info: 0, warn: 0, error: 0 };
    for (const e of entries) c[e.level]++;
    return c;
  }, [entries]);

  // Follow the end of the log unless the user has scrolled up to read.
  const list = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = list.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [visible]);

  const toggle = (level: ConsoleLevel) =>
    setShown((was) => {
      const next = new Set(was);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  const copy = () =>
    void navigator.clipboard?.writeText(visible.map((e) => `${time(e.t)} ${e.level.toUpperCase()} ${e.text}`).join("\n"));

  return (
    <div className="flex flex-col gap-1.5 text-xs h-full min-h-0">
      <div className="flex items-center gap-1 flex-wrap">
        {CONSOLE_LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            aria-pressed={shown.has(level)}
            onClick={() => toggle(level)}
            className={`px-1.5 py-0.5 rounded border ${
              shown.has(level) ? `border-white/30 ${LEVEL_STYLE[level]}` : "border-transparent text-gray-600"
            }`}
          >
            {level} {counts[level] > 0 && <span className="text-gray-400">{counts[level]}</span>}
          </button>
        ))}
        <input
          className="flex-1 min-w-[6rem] bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 text-gray-100"
          placeholder="Filter"
          aria-label="Filter the log"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="button" className="text-gray-300 hover:text-white px-1" onClick={copy} title="Copy the shown entries">
          Copy
        </button>
        <button type="button" className="text-gray-300 hover:text-white px-1" onClick={clear} title="Clear the log">
          Clear
        </button>
      </div>
      <div
        ref={list}
        role="log"
        aria-label="Console"
        className="flex-1 min-h-[12rem] max-h-[50vh] overflow-auto font-mono text-[11px] leading-snug bg-black/30 rounded-sm p-1"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
        }}
      >
        {visible.length === 0 ? (
          <div className="text-gray-500">{entries.length ? "Nothing matches." : "Nothing logged yet."}</div>
        ) : (
          visible.map((e) => (
            <div key={e.id} className={`whitespace-pre-wrap break-words ${LEVEL_STYLE[e.level]}`}>
              <span className="text-gray-500">{time(e.t)} </span>
              {e.text}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

/** The classic frame: a dark card in the menu column, shown while the panel is toggled on. */
const ConsolePanel: React.FC = () => {
  const isVisible = useConsoleLogStore((s) => s.isVisible);
  const hosted = usePanelHeaderActions().length > 0;
  if (!isVisible) return null;
  return (
    <div className="flex flex-col gap-2 text-white p-2 bg-gray-900/80 rounded-md w-[560px] max-w-[90vw]">
      {hosted && (
        <div className="flex items-center gap-2 text-xs">
          <span className="font-bold">Console</span>
          <PanelHeaderActions />
        </div>
      )}
      <ConsoleBody />
    </div>
  );
};

export default ConsolePanel;
