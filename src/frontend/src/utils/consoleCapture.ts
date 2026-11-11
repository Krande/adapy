// Copy what the page logs into the Console panel's store (state/consoleLogStore): every
// `console.debug/log/info/warn/error` call -- still passed on to the real console -- and uncaught
// errors and unhandled promise rejections, which never reach `console` on their own.
//
// Installed once, first thing in the entry (index.tsx), so the panel shows the start-up too. Entries
// are flushed in batches on a short timer: a burst of logging is one store update, and a logging call
// made while the store updates (a React warning) cannot recurse into it.

import { appendConsoleEntries, type ConsoleEntry, type ConsoleLevel } from "@/state/consoleLogStore";

const FLUSH_MS = 100;
/** One entry's text is cut at this many characters (a dumped mesh would fill the panel). */
const MAX_TEXT = 4000;

let installed = false;
let nextId = 1;
let pending: ConsoleEntry[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function record(level: ConsoleLevel, text: string): void {
  pending.push({ id: nextId++, t: Date.now(), level, text: text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text });
  if (timer === null) {
    timer = setTimeout(() => {
      timer = null;
      const batch = pending;
      pending = [];
      appendConsoleEntries(batch);
    }, FLUSH_MS);
  }
}

/** A console argument as text: strings as they are, errors with their stack, objects as JSON. */
export function formatConsoleArg(arg: unknown): string {
  if (typeof arg === "string") return arg;
  if (arg instanceof Error) return arg.stack ?? `${arg.name}: ${arg.message}`;
  if (arg === undefined) return "undefined";
  if (typeof arg === "function") return `[function ${arg.name || "anonymous"}]`;
  if (typeof arg !== "object" || arg === null) return String(arg);
  try {
    const seen = new WeakSet<object>();
    return JSON.stringify(arg, (_k, v) => {
      if (typeof v === "object" && v !== null) {
        if (seen.has(v)) return "[circular]";
        seen.add(v);
      }
      return typeof v === "bigint" ? `${v}n` : v;
    });
  } catch {
    return String(arg);
  }
}

/** Start copying console output into the Console panel's store. Idempotent. */
export function installConsoleCapture(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  for (const level of ["debug", "log", "info", "warn", "error"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      original(...args);
      try {
        record(level, args.map(formatConsoleArg).join(" "));
      } catch {
        // Capturing must never break the caller's logging.
      }
    };
  }
  window.addEventListener("error", (e) => {
    const where = e.filename ? ` (${e.filename}:${e.lineno}:${e.colno})` : "";
    record("error", `Uncaught ${e.error ? formatConsoleArg(e.error) : e.message}${where}`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    record("error", `Unhandled rejection: ${formatConsoleArg(e.reason)}`);
  });
}
