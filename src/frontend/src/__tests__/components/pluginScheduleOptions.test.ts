import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RESERVED_OPTIONS_KEY,
  parseScheduleOptions,
} from "@/components/admin/pluginScheduleOptions";

// The options document is the one field on a plugin-job schedule that no control
// can constrain — whatever the plugin understands goes in it. These are the rules
// the form applies before spending a round trip, and the reserved-key rule is the
// one that matters: without it a schedule can be created that cache-hits its own
// first run forever and reports success every hour.

test("an empty box means no options, not a malformed document", () => {
  // A plugin whose job takes no arguments is the simplest thing to schedule and
  // must not require the operator to type "{}".
  assert.deepEqual(parseScheduleOptions(""), { options: {} });
  assert.deepEqual(parseScheduleOptions("   \n  "), { options: {} });
});

test("an object is returned as-is", () => {
  const r = parseScheduleOptions('{"action": "changes", "since_days": 2}');
  assert.deepEqual(r, { options: { action: "changes", since_days: 2 } });
});

test("whitespace and newlines around a document are fine", () => {
  const r = parseScheduleOptions('\n  {\n  "action": "changes"\n }\n');
  assert.deepEqual(r, { options: { action: "changes" } });
});

test("a syntax error explains itself rather than reaching the API", () => {
  const r = parseScheduleOptions('{"action": "changes",}');
  assert.ok("error" in r, "a trailing comma was accepted");
  assert.match(r.error, /not valid JSON/);
});

test("a non-object names the shape the API wants", () => {
  // Each of these is valid JSON, so only this check stands between them and a
  // 400 whose message talks about mappings.
  for (const text of ["[1, 2]", '"changes"', "42", "null", "true"]) {
    const r = parseScheduleOptions(text);
    assert.ok("error" in r, `${text} was accepted as an options object`);
    assert.match(r.error, /must be a JSON object/);
  }
});

test("the key the scheduler stamps is refused, with the reason", () => {
  // Core hashes the options into the job's source key, so byte-identical options
  // cache-hit: the tick varies them by stamping this key on every firing. A
  // schedule that sets it itself would be silently overwritten, and the operator
  // who set it believed it meant something.
  const r = parseScheduleOptions(`{"${RESERVED_OPTIONS_KEY}": "2026-09-09T00:00:00Z"}`);
  assert.ok("error" in r, "the reserved key was accepted");
  assert.match(r.error, new RegExp(RESERVED_OPTIONS_KEY));
  assert.match(r.error, /cache-hit/, "the error does not say why it is reserved");
});

test("the reserved key is refused alongside legitimate options", () => {
  const r = parseScheduleOptions(`{"action": "changes", "${RESERVED_OPTIONS_KEY}": 1}`);
  assert.ok("error" in r, "the reserved key slipped through next to another key");
});

test("a key that merely resembles the reserved one is allowed", () => {
  // The server refuses exactly one key. Rejecting near-misses here would refuse
  // options the API would have accepted, which is the worse failure: the
  // operator cannot tell it from the plugin not supporting the option.
  const r = parseScheduleOptions('{"scheduled_at_local": "…", "scheduled": true}');
  assert.ok(!("error" in r), "a similarly-named key was refused");
});
