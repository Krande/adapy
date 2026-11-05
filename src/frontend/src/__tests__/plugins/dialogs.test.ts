// The dialog service: a host's dialogs answer while installed, core's otherwise.

import assert from "node:assert/strict";
import { test } from "node:test";

import { alertDialog, confirmDialog, promptDialog, setDialogHost } from "@/plugins/dialogs";
import type { ConfirmOptions } from "@/plugins/dialogs";

test("an installed host answers every kind it provides", async () => {
  const asked: string[] = [];
  const uninstall = setDialogHost({
    confirm: async (o) => (asked.push(`confirm:${o.title}`), true),
    alert: async (o) => void asked.push(`alert:${o.title}`),
    prompt: async (o) => (asked.push(`prompt:${o.title}`), `${o.initial}!`),
  });
  try {
    assert.equal(await confirmDialog({ title: "A" }), true);
    await alertDialog({ title: "B" });
    assert.equal(await promptDialog({ title: "C", initial: "x" }), "x!");
    assert.deepEqual(asked, ["confirm:A", "alert:B", "prompt:C"]);
  } finally {
    uninstall();
  }
});

test("a kind the host leaves out, and everything after uninstall, falls back to core's", async () => {
  // Core's dialogs need a page; without one they decline.
  const uninstall = setDialogHost({ alert: async () => {} });
  assert.equal(await confirmDialog({ title: "A" }), false);
  assert.equal(await promptDialog({ title: "B" }), null);
  uninstall();
  const seen: ConfirmOptions[] = [];
  const again = setDialogHost({ confirm: async (o) => (seen.push(o), true) });
  again();
  assert.equal(await confirmDialog({ title: "C" }), false);
  assert.deepEqual(seen, []);
});

test("uninstalling a replaced host leaves its replacement in place", async () => {
  const first = setDialogHost({ confirm: async () => false });
  setDialogHost({ confirm: async () => true });
  first();
  try {
    assert.equal(await confirmDialog({ title: "A" }), true);
  } finally {
    setDialogHost(null);
  }
});
