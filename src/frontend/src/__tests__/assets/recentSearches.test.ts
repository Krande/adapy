import assert from "node:assert/strict";
import { test } from "node:test";

import { RECENT_SEARCHES_KEY, loadRecent, recentFor, saveRecent, withRecent } from "../../assets/recentSearches";

test("a searched term goes to the front, once, and at most five are kept", () => {
  let list: string[] = [];
  for (const t of ["/PIPE-1", "/PIPE-2", "/PIPE-3", "/PIPE-4", "/PIPE-5", "/PIPE-6"]) list = withRecent(list, t);
  assert.deepEqual(list, ["/PIPE-6", "/PIPE-5", "/PIPE-4", "/PIPE-3", "/PIPE-2"]);
  // a repeat in another case moves to the front, spelled as typed now
  assert.deepEqual(withRecent(list, "/pipe-3"), ["/pipe-3", "/PIPE-6", "/PIPE-5", "/PIPE-4", "/PIPE-2"]);
  // trimmed; a term too short to search is not remembered
  assert.deepEqual(withRecent([], "  /SITE-A  "), ["/SITE-A"]);
  assert.deepEqual(withRecent(["/SITE-A"], "x"), ["/SITE-A"]);
  assert.deepEqual(withRecent(["/SITE-A"], "   "), ["/SITE-A"]);
});

test("the dropdown offers all while empty or on a remembered term, else those containing the text", () => {
  const list = ["/PIPE-1", "/SITE-A", "/pipe-2"];
  assert.deepEqual(recentFor(list, ""), list);
  assert.deepEqual(recentFor(list, "/SITE-A"), list);
  assert.deepEqual(recentFor(list, "pipe"), ["/PIPE-1", "/pipe-2"]);
  assert.deepEqual(recentFor(list, "zzz"), []);
});

test("storage round-trips, and a broken or missing store means no history, never an error", () => {
  const mem = new Map<string, string>();
  const store = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
  saveRecent(["/PIPE-1", "/SITE-A"], store);
  assert.deepEqual(loadRecent(store), ["/PIPE-1", "/SITE-A"]);
  mem.set(RECENT_SEARCHES_KEY, "{not json");
  assert.deepEqual(loadRecent(store), []);
  mem.set(RECENT_SEARCHES_KEY, JSON.stringify(["/A-1", 3, "", "/B-2"]));
  assert.deepEqual(loadRecent(store), ["/A-1", "/B-2"]);
  const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.deepEqual(loadRecent(throwing), []);
  saveRecent(["/A-1"], throwing); // does not throw
  assert.deepEqual(loadRecent(undefined), []);
});
