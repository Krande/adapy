// Provider options: the per-collection document, what a request sends from it, and the choices a
// provider's job describes.

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  EMPTY_PROVIDER_OPTIONS,
  PROVIDER_OPTIONS_SCHEMA,
  parseOptionChoices,
  parseProviderOptionsDoc,
  providerOptionsKey,
  requestValues,
  serialiseProviderOptionsDoc,
  withListedChoices,
  withProviderValues,
} from "../../assets/providerOptions";

test("the document lives beside the view and the sets, one per collection", () => {
  assert.equal(providerOptionsKey("abc"), "assets/_options/abc.json");
});

test("the provider's spelling of a collection finds the same document as the tab's key", () => {
  // The request panel names a collection as the provider advertises it, the Sources tab by its
  // lower-case key; a tree request has to read what the Options panel saved.
  assert.equal(providerOptionsKey("ABC"), providerOptionsKey("abc"));
});

test("empty reads as nothing set; an unknown schema refuses rather than being overwritten", () => {
  assert.deepEqual(parseProviderOptionsDoc(""), EMPTY_PROVIDER_OPTIONS);
  assert.deepEqual(parseProviderOptionsDoc(null), EMPTY_PROVIDER_OPTIONS);
  assert.throws(() => parseProviderOptionsDoc("{"), /not JSON/);
  assert.throws(() => parseProviderOptionsDoc({ schema: "ada.assets/provider-options@2" }), /will not write over/);
});

test("malformed providers are dropped; values are kept as stored", () => {
  const doc = parseProviderOptionsDoc({
    schema: PROVIDER_OPTIONS_SCHEMA,
    providers: { p1: { dbs: ["A"], flag: true }, p2: "nope", "": { x: 1 } },
  });
  assert.deepEqual(doc.providers, { p1: { dbs: ["A"], flag: true } });
  assert.deepEqual(parseProviderOptionsDoc(serialiseProviderOptionsDoc(doc)).providers, doc.providers);
});

test("saving one provider keeps the others, drops unset values, and removes an emptied provider", () => {
  let doc = withProviderValues(EMPTY_PROVIDER_OPTIONS, "p1", { dbs: ["A"], name: "" }, "t1");
  doc = withProviderValues(doc, "p2", { dbs: ["B"] }, "t2");
  assert.deepEqual(doc.providers, { p1: { dbs: ["A"] }, p2: { dbs: ["B"] } });
  assert.equal(doc.updated_at, "t2");
  doc = withProviderValues(doc, "p1", { dbs: [] }, "t3");
  assert.deepEqual(doc.providers, { p2: { dbs: ["B"] } });
});

test("a request sends the provider's set values for the options it declares, nothing else", () => {
  const doc = parseProviderOptionsDoc({
    schema: PROVIDER_OPTIONS_SCHEMA,
    providers: { p1: { dbs: ["A"], gone: "x", off: false, empty: [] } },
  });
  assert.deepEqual(requestValues(doc, "p1", ["dbs", "off", "empty"]), { dbs: ["A"], off: false });
  assert.deepEqual(requestValues(doc, "other", ["dbs"]), {});
});

test("listed choices are kept as a cache beside the values, and survive a save of the values", () => {
  let doc = withProviderValues(EMPTY_PROVIDER_OPTIONS, "p1", { dbs: ["A"] }, "t1");
  doc = withListedChoices(doc, "p1", { dbs: [{ value: "A", label: "A", description: "2 sites" }] }, "t2");
  doc = withProviderValues(doc, "p1", { dbs: ["A", "B"] }, "t3");
  const back = parseProviderOptionsDoc(serialiseProviderOptionsDoc(doc));
  assert.deepEqual(back.providers, { p1: { dbs: ["A", "B"] } });
  assert.deepEqual(back.choices, { p1: { listed_at: "t2", options: { dbs: [{ value: "A", label: "A", description: "2 sites" }] } } });
  // The cache is never what a request sends.
  assert.deepEqual(requestValues(back, "p1", ["dbs", "choices"]), { dbs: ["A", "B"] });
});

test("a malformed cache entry is dropped, not guessed at", () => {
  const doc = parseProviderOptionsDoc({
    schema: PROVIDER_OPTIONS_SCHEMA,
    providers: {},
    choices: { p1: { options: { dbs: [{ value: "A" }] } }, p2: "nope", p3: { listed_at: "t", options: { dbs: [{ value: "B" }, 3] } } },
  });
  assert.deepEqual(doc.choices, { p3: { listed_at: "t", options: { dbs: [{ value: "B", label: "B" }] } } });
});

test("choices are read off the summary; malformed ones dropped, duplicates once", () => {
  assert.deepEqual(parseOptionChoices({}), {});
  assert.deepEqual(parseOptionChoices(null), {});
  assert.deepEqual(
    parseOptionChoices({
      option_choices: {
        dbs: [{ value: "A", label: "Alpha", description: "2 sites" }, { value: "A" }, "B", { label: "no value" }, 7],
        bad: "nope",
      },
    }),
    { dbs: [{ value: "A", label: "Alpha", description: "2 sites" }, { value: "B", label: "B" }] },
  );
});
