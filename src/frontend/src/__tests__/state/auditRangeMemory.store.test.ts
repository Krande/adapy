import assert from "node:assert/strict";
import { test } from "node:test";

// The Audit tab opens on a bounded window (24h) rather than on the whole
// table, and an operator who wants a different default sets it by using the
// range control they already use — the choice is remembered. That is the only
// filter in this store that survives a reload, so the behaviour is pinned here
// rather than left to the component.
//
// localStorage does not exist in a bare node test process, so a stub is
// installed BEFORE the store module is imported: the opening window is read at
// module init, and a static import would run that read first.

const KEY = "ada-audit-range";

const backing = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (backing.has(k) ? (backing.get(k) as string) : null),
  setItem: (k: string, v: string): void => {
    backing.set(k, String(v));
  },
  removeItem: (k: string): void => {
    backing.delete(k);
  },
  clear: (): void => backing.clear(),
  key: (): string | null => null,
  get length(): number {
    return backing.size;
  },
};
(globalThis as unknown as { localStorage: unknown }).localStorage = storage;

const { AUDIT_DEFAULT_RANGE, useAuditFilterStore } = await import("@/state/auditFilterStore");

// Captured before any test can move it: this is what the panel showed on load.
const openingWindow = useAuditFilterStore.getState().filters.since;

const s = () => useAuditFilterStore.getState();

test("with nothing remembered, the panel opens on the built-in default", () => {
  assert.equal(openingWindow, AUDIT_DEFAULT_RANGE);
});

test("the window an operator picks becomes the window the panel opens on", () => {
  backing.clear();
  s().patch({ since: "1h", until: undefined });
  assert.equal(backing.get(KEY), "1h", "the chosen window was not remembered");
  // reset() is "back to where the panel opens", so it is also the assertion
  // that the remembered value is what a fresh load would read.
  s().patch({ status: "error" });
  s().reset();
  assert.equal(s().filters.since, "1h");
});

test("choosing all time is remembered as readily as narrowing", () => {
  // All time is spelled `since: undefined` by the range control. Ignoring it
  // would make the one choice an operator cannot make stick the one they are
  // most likely to want for an investigation spanning days.
  backing.clear();
  s().patch({ since: undefined, until: undefined });
  assert.equal(backing.get(KEY), "");
  s().reset();
  assert.equal(s().filters.since, undefined);
});

test("a custom absolute range is applied but not remembered", () => {
  // "since 2026-08-01T00:00" answers one question today and is stale next
  // week. Remembering it would leave a default nobody recognises as theirs.
  backing.clear();
  s().patch({ since: "6h", until: undefined });
  s().patch({ since: "2026-08-01T00:00:00.000Z", until: "2026-08-02T00:00:00.000Z" });
  assert.equal(s().filters.since, "2026-08-01T00:00:00.000Z", "the custom range was not applied");
  assert.equal(backing.get(KEY), "6h", "the custom range overwrote the remembered preset");
  s().reset();
  assert.equal(s().filters.since, "6h");
});

test("an unrecognised stored value falls back to the built-in default", () => {
  // Storage is shared with every other tab and survives a release that drops a
  // range from the ladder. A value that is no longer a preset must not reach
  // the API, where it would 400, or the picker, where it would read as custom.
  backing.clear();
  backing.set(KEY, "fortnight");
  s().reset();
  assert.equal(s().filters.since, AUDIT_DEFAULT_RANGE);
});

test("storage that refuses to answer does not break the panel", () => {
  // A private window, blocked site data, or a browser that throws on access.
  // The panel still opens, and changing the range still works — it just does
  // not outlive the tab.
  const boom = () => {
    throw new Error("SecurityError");
  };
  const realGet = storage.getItem;
  const realSet = storage.setItem;
  storage.getItem = boom as unknown as typeof storage.getItem;
  storage.setItem = boom as unknown as typeof storage.setItem;
  try {
    s().reset();
    assert.equal(s().filters.since, AUDIT_DEFAULT_RANGE);
    s().patch({ since: "6h", until: undefined });
    assert.equal(s().filters.since, "6h", "the range control stopped working without storage");
  } finally {
    storage.getItem = realGet;
    storage.setItem = realSet;
  }
});
