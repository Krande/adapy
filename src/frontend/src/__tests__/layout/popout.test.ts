// A panel in a browser window of its own: who may, and the window's URL.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { DEFAULT_PANEL_CAPABILITIES } from "@/layout/panel";
import { SESSION_PARAM, canPopOut, popoutPanelId, popoutSession, popoutUrl, popoutWindowId, windowTarget, windowUrl } from "@/layout/popout";
import { getPanel, resetPanelRegistry } from "@/layout/registry";
import { registerPlugin, resetRegistry } from "@/plugins/registry";

const body = () => Promise.resolve({ default: () => null });
const panel = (caps: Partial<typeof DEFAULT_PANEL_CAPABILITIES>, constraints?: { requiresCanvas?: boolean }) => ({
  capabilities: { ...DEFAULT_PANEL_CAPABILITIES, ...caps },
  constraints,
  body,
});

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
});

test("only a panel that opts in and needs no canvas may pop out", () => {
  assert.equal(canPopOut(panel({})), false, "off by default");
  assert.equal(canPopOut(panel({ popout: true })), true);
  assert.equal(canPopOut(panel({ popout: true }, { requiresCanvas: true })), false);
});

test("the window's URL names the panel, keeps the UI, plugins and scope, and drops what loads models", () => {
  const url = new URL(popoutUrl("demo:hello", "https://viewer.example/app/?ui=alt&plugins=demo&scope=user:me&file=a.glb#x"));
  assert.equal(url.pathname, "/app/");
  assert.equal(url.searchParams.get("popout"), "demo:hello");
  assert.equal(url.searchParams.get("ui"), "alt");
  assert.equal(url.searchParams.get("plugins"), "demo");
  assert.equal(url.searchParams.get("scope"), "user:me");
  assert.equal(url.searchParams.get("file"), null);
  assert.equal(url.hash, "");
  assert.equal(popoutPanelId(url.search), "demo:hello");
  assert.equal(popoutPanelId("?ui=alt"), null);
});

test("a window's URL carries the dashboard session next to its id; the browser window name does too", () => {
  const url = new URL(windowUrl("w2", "demo:hello", "https://viewer.example/app/?ui=alt&file=a.glb", "abc12345"));
  assert.equal(url.searchParams.get("window"), "w2");
  assert.equal(url.searchParams.get(SESSION_PARAM), "abc12345");
  assert.equal(url.searchParams.get("popout"), "demo:hello");
  assert.equal(popoutSession(url.search), "abc12345");
  assert.equal(popoutWindowId(url.search), "w2");
  assert.equal(new URL(popoutUrl("demo:hello", "https://viewer.example/", "abc12345")).searchParams.get("session"), "abc12345");
  // Without a session (an old link): none in the URL, none read.
  assert.equal(new URL(windowUrl("w2", undefined, "https://viewer.example/")).searchParams.get("session"), null);
  assert.equal(popoutSession("?window=w2"), null);
  assert.equal(popoutSession("?session=bad%20id"), null, "malformed");
  assert.notEqual(windowTarget("w2", "aaaa1111"), windowTarget("w2", "bbbb2222"), "two sessions' w2 are two browser windows");
  assert.equal(windowTarget("w2", "aaaa1111"), windowTarget("w2", "aaaa1111"));
});

test("a plugin slot opts in with `popout`", () => {
  const render = () => null;
  registerPlugin({ id: "p", panels: [{ id: "a", region: "top-panel", render, popout: true }, { id: "b", region: "top-panel", render }] });
  assert.equal(getPanel("p:a")?.capabilities.popout, true);
  assert.equal(getPanel("p:b")?.capabilities.popout, false);
  assert.equal(canPopOut(getPanel("p:a")!), true);
});
