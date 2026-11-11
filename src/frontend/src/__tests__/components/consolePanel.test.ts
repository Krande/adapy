/**
 * The Console panel: what the page logs is captured (still reaching the real console), kept to the
 * last CONSOLE_LOG_MAX entries, filtered by level and text -- and the panel is in Ctrl+K's list.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { listCommands } from "../../layout/commands";
import { CORE_PANEL_IDS, registerCorePanels } from "../../layout/corePanels";
import { filterConsoleEntries } from "../../components/console/ConsolePanel";
import { appendConsoleEntries, CONSOLE_LOG_MAX, useConsoleLogStore, type ConsoleEntry } from "../../state/consoleLogStore";
import { formatConsoleArg } from "../../utils/consoleCapture";

const entry = (id: number, level: ConsoleEntry["level"], text: string): ConsoleEntry => ({ id, t: 0, level, text });

test("the store keeps the newest CONSOLE_LOG_MAX entries", () => {
  useConsoleLogStore.getState().clear();
  appendConsoleEntries(Array.from({ length: CONSOLE_LOG_MAX + 5 }, (_, i) => entry(i, "log", `line ${i}`)));
  const { entries } = useConsoleLogStore.getState();
  assert.equal(entries.length, CONSOLE_LOG_MAX);
  assert.equal(entries[0].text, "line 5");
  useConsoleLogStore.getState().clear();
  assert.equal(useConsoleLogStore.getState().entries.length, 0);
});

test("entries filter by level and, case-insensitively, by text", () => {
  const all = [entry(1, "log", "Loaded model"), entry(2, "warn", "slow MODEL"), entry(3, "error", "boom")];
  assert.deepEqual(
    filterConsoleEntries(all, new Set(["warn", "error"]), "").map((e) => e.id),
    [2, 3],
  );
  assert.deepEqual(
    filterConsoleEntries(all, new Set(["log", "warn", "error"]), "model").map((e) => e.id),
    [1, 2],
  );
});

test("console arguments read as text: errors with their stack, objects as JSON, cycles marked", () => {
  assert.equal(formatConsoleArg("plain"), "plain");
  assert.equal(formatConsoleArg(3), "3");
  assert.equal(formatConsoleArg(undefined), "undefined");
  assert.equal(formatConsoleArg({ a: 1 }), '{"a":1}');
  const cyclic: Record<string, unknown> = { name: "x" };
  cyclic.self = cyclic;
  assert.equal(formatConsoleArg(cyclic), '{"name":"x","self":"[circular]"}');
  const err = new Error("bad");
  assert.match(formatConsoleArg(err), /Error: bad/);
});

test("Ctrl+K finds the Console panel", () => {
  registerCorePanels();
  const hit = listCommands().find((c) => c.id === `panel:${CORE_PANEL_IDS.console}`);
  assert.ok(hit, "the Console panel is a palette command");
  assert.equal(hit.label, "Console");
  assert.equal(hit.group, "Panels");
});
