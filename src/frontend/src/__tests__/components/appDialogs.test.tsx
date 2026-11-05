// Core's in-app dialog: an accessible modal per request kind.

import assert from "node:assert/strict";
import { test } from "node:test";

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AppDialog } = await import("@/components/ui/AppDialogs");

type Request = Parameters<typeof AppDialog>[0]["request"];
const render = (request: Request) => renderToStaticMarkup(React.createElement(AppDialog, { request, onDone: () => {} }));

test("a confirmation is a labelled modal alertdialog with both answers", () => {
  const html = render({
    kind: "confirm",
    options: { title: "Delete it?", body: "Line one\nLine two", confirmLabel: "Delete", tone: "danger" },
    resolve: () => {},
  });
  assert.match(html, /role="alertdialog"/);
  assert.match(html, /aria-modal="true"/);
  assert.match(html, /aria-labelledby="[^"]+"/);
  assert.match(html, /aria-describedby="[^"]+"/);
  assert.match(html, /<p>Line one<\/p><p>Line two<\/p>/);
  assert.match(html, />Cancel</);
  assert.match(html, /bg-red-700[^>]*>Delete</);
});

test("an alert has one button; a prompt is a dialog with its input", () => {
  const alert = render({ kind: "alert", options: { title: "Done" }, resolve: () => {} });
  assert.doesNotMatch(alert, />Cancel</);
  assert.match(alert, />OK</);
  assert.doesNotMatch(alert, /aria-describedby/);
  const prompt = render({
    kind: "prompt",
    options: { title: "Name", label: "New name", initial: "a" },
    resolve: () => {},
  });
  assert.match(prompt, /role="dialog"/);
  assert.match(prompt, /New name/);
  assert.match(prompt, /<input[^>]*value="a"/);
});
