import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

// The classic top bar's "Top bar buttons" setting (Settings > Theme): which buttons the user turned
// off, kept per browser. The stub is installed before the store is imported, since it hydrates at
// module init.

const backing = new Map<string, string>([
  // As a previous page load stored it, with one id that is pinned and one value that is not an id.
  ["ada-toolbar-buttons", JSON.stringify({ state: { hidden: ["core:scene", "core:options", 7] }, version: 0 })],
]);
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: (k: string): string | null => backing.get(k) ?? null,
  setItem: (k: string, v: string): void => void backing.set(k, String(v)),
  removeItem: (k: string): void => void backing.delete(k),
  clear: (): void => backing.clear(),
  key: (): string | null => null,
  get length(): number {
    return backing.size;
  },
};

const {
  PINNED_TOOLBAR_BUTTONS,
  canHideToolbarButton,
  isToolbarButtonShown,
  toolbarButtonChoices,
  useToolbarButtonsStore,
} = await import("@/state/toolbarButtonsStore");
const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");

const stored = () => JSON.parse(backing.get("ada-toolbar-buttons") ?? "{}").state?.hidden;

test("the stored choice is read back, dropping values that are not panel ids", () => {
  assert.deepEqual(useToolbarButtonsStore.getState().hidden, ["core:scene", "core:options"]);
  assert.equal(isToolbarButtonShown(useToolbarButtonsStore.getState().hidden, "core:scene"), false);
});

test("the Options button can never be hidden: it is the way back to Settings", () => {
  assert.ok(PINNED_TOOLBAR_BUTTONS.has(CORE_PANEL_IDS.options));
  assert.equal(canHideToolbarButton(CORE_PANEL_IDS.options), false);
  // Even with its id stored as hidden.
  assert.equal(isToolbarButtonShown([CORE_PANEL_IDS.options], CORE_PANEL_IDS.options), true);
  const before = useToolbarButtonsStore.getState().hidden;
  useToolbarButtonsStore.getState().setButtonShown(CORE_PANEL_IDS.options, false);
  assert.equal(useToolbarButtonsStore.getState().hidden, before);
});

test("hiding and showing a button is written to storage", () => {
  const s = useToolbarButtonsStore.getState();
  s.showAll();
  assert.deepEqual(stored(), []);
  s.setButtonShown("core:tree", false);
  s.setButtonShown("my-plugin:panel", false);
  s.setButtonShown("core:tree", false); // already hidden: no duplicate
  assert.deepEqual(stored(), ["core:tree", "my-plugin:panel"]);
  assert.equal(isToolbarButtonShown(useToolbarButtonsStore.getState().hidden, "my-plugin:panel"), false);
  s.setButtonShown("core:tree", true);
  assert.deepEqual(stored(), ["my-plugin:panel"]);
  assert.equal(isToolbarButtonShown(useToolbarButtonsStore.getState().hidden, "core:tree"), true);
  s.showAll();
  assert.deepEqual(stored(), []);
});

test("the choices keep the top bar's order, leave out pinned and absent buttons, and label by title", () => {
  const panels = [
    { id: CORE_PANEL_IDS.options, title: "Options", hint: "Toggle options drawer" },
    { id: CORE_PANEL_IDS.tree, title: "Selection tree" },
    { id: CORE_PANEL_IDS.storage, title: "Storage" },
    { id: "p:runs", title: "", hint: "Runs" },
    { id: CORE_PANEL_IDS.scene, title: "Scene info", hint: "Scene info" },
  ];
  const choices = toolbarButtonChoices(panels, (id) => id !== CORE_PANEL_IDS.storage);
  assert.deepEqual(choices, [
    { id: CORE_PANEL_IDS.tree, label: "Selection tree" },
    { id: "p:runs", label: "Runs" },
    { id: CORE_PANEL_IDS.scene, label: "Scene info" },
  ]);
});

const here = dirname(fileURLToPath(import.meta.url));
const src = (p: string) => readFileSync(resolve(here, "../..", p), "utf8");

test("the classic top bar filters both core and plugin buttons by the setting", () => {
  const classic = src("layout/presets/classic.tsx");
  assert.match(classic, /useToolbarButtonsStore\(\(s\) => s\.hidden\)/);
  assert.match(classic, /p\.owner === CORE_PANEL_OWNER && shown\(p\.id\)/);
  assert.match(classic, /<PluginTopBarButtons .*isShown=\{shown\} \/>/);
  // The plugin host only drops the button; the panel region does not read the setting.
  const slots = src("plugins/PluginSlots.tsx");
  assert.match(slots, /panel\.topBarButton && \(!isShown \|\| isShown\(panel\.id\)\)/);
  assert.doesNotMatch(slots, /toolbarButtonsStore/);
});

test("the setting is in the Settings window's Theme tab", () => {
  const sections = src("components/settings/settingsSections.tsx");
  const theme = sections.slice(sections.indexOf('id: "theme"'), sections.indexOf('id: "performance"'));
  assert.match(theme, /<ToolbarButtonsOptions/);
});

test("on a narrow screen the button row scrolls sideways with no scrollbar; Options and the status menu stay pinned", () => {
  const classic = src("layout/presets/classic.tsx");
  const bar = classic.slice(classic.indexOf("function ClassicToolbar()"), classic.indexOf("// The menu column."));
  const row = /<div className="([^"]*)" data-toolbar-scroll="">([\s\S]*?)<\/div>/.exec(bar);
  assert.ok(row, "the scrolling row");
  for (const cls of ["overflow-x-auto", "scrollbar-none", "min-w-0", "[&>*]:shrink-0"]) assert.ok(row[1].split(" ").includes(cls), cls);
  assert.doesNotMatch(row[1], /flex-wrap/);
  // The plugin buttons scroll with core's; the pinned ones are drawn outside the row.
  assert.match(row[2], /<PluginTopBarButtons/);
  assert.doesNotMatch(row[2], /start\.map|end\.map|MinimisedTray/);
  assert.match(bar, /p\.id === CORE_PANEL_IDS\.options/);
  assert.match(bar, /CLASSIC_TOOLBAR\[p\.id\]\?\.statusMenu/);
  // The scrollbar is hidden in every engine.
  const css = src("app.css");
  const util = css.slice(css.indexOf("@utility scrollbar-none"), css.indexOf("}", css.indexOf("display: none")) + 1);
  for (const rule of ["scrollbar-width: none", "-ms-overflow-style: none", "::-webkit-scrollbar", "display: none"]) assert.ok(util.includes(rule), rule);
});
