// The console of the run on screen as a panel of the layout (`core:run-console`): how it is
// registered, where it starts (a window over the top right of the view, once), where it shows when
// panels are not customisable or the page is narrow, the playback transport's button that opens
// it, and what its body draws.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { CORE_PANEL_IDS, CORE_PANEL_OWNER, registerCorePanels } = await import("@/layout/corePanels");
const { getPanel, isPanelAvailable, listToolbarPanels } = await import("@/layout/registry");
const { hide, isPanelOpen, reveal, toggle } = await import("@/layout/reveal");
const { layoutStore } = await import("@/layout/layoutStore");
const { setFloatRect } = await import("@/layout/reducers");
const { groupOf, VIEWPORT_NODE } = await import("@/layout/splitTree");
const { gridOf } = await import("@/layout/reducers");
const P = await import("@/layout/presets/classicPlace");
const { columnActions, floatActions } = await import("@/layout/presets/classicUndock");
const { classicMounts } = await import("@/layout/presets/classicRules");
const { CLASSIC_PANEL_COMPONENTS, CLASSIC_PANEL_CONTENTS } = await import("@/layout/classicBodies");
const { playbackClock } = await import("@/state/playbackClock");
const { useRunConsoleStore } = await import("@/state/runConsoleStore");
const { ActivityFeed } = await import("@/services/simulations/activityFeed");
const { default: RunConsole, RunConsoleBody } = await import("@/components/simulations/RunConsole");
const { default: PlaybackTransport } = await import("@/components/playback/PlaybackTransport");

registerCorePanels();
const ID = CORE_PANEL_IDS.runConsole;
const panel = () => getPanel(ID)!;
const RUN = { scope: "shared", runId: "run-1", title: "Run one" } as const;
const track = () => playbackClock.addTrack({ id: "t", duration: 30, show() {} });
const freshLayout = () => layoutStore.setState({ prefs: { v: 1, docs: {} } });
/** A page for windows to be placed in: the test realm's has no size of its own. */
const page = globalThis as unknown as { window: { innerWidth?: number; innerHeight?: number } };
Object.assign(page.window, { innerWidth: 1280, innerHeight: 800 });

function feed(): InstanceType<typeof ActivityFeed> {
  const lines = [
    { schema: "ada-activity/1", actors: [{ id: "a", label: "First", color: "#4db3e6" }, { id: "b", label: "Second" }] },
    { t: 0, actor: "a", kind: "state", text: "Moving to the start" },
    { t: 1, actor: "a", kind: "plan", text: "Wait for the other" },
    { t: 2, actor: "b", text: "Nothing arrived", level: "warn" },
    { t: 20, actor: "b", kind: "state", text: "Done" },
  ];
  const f = new ActivityFeed();
  f.append(new TextEncoder().encode(lines.map((l) => JSON.stringify(l)).join("\n") + "\n"));
  return f;
}

test("the console is a core panel bound to its flag, with no top-bar button, offered while a run's result is on screen", () => {
  const p = panel();
  assert.equal(p.owner, CORE_PANEL_OWNER);
  assert.equal(p.title, "Run console");
  assert.ok(!listToolbarPanels().some((t) => t.id === ID), "the transport's button opens it, not the top bar");
  assert.equal(typeof p.content, "function");
  assert.ok(CLASSIC_PANEL_COMPONENTS[ID] && CLASSIC_PANEL_CONTENTS[ID], "its card and its frameless content are the shell's");

  // The flag is the store's `shown`, written by its own setter.
  useRunConsoleStore.getState().setShown(false);
  assert.equal(isPanelOpen(ID), false);
  reveal(ID);
  assert.equal(useRunConsoleStore.getState().shown, true);
  assert.equal(isPanelOpen(ID), true);
  hide(ID);
  assert.equal(useRunConsoleStore.getState().shown, false);

  // Offered while a run is named and something timed is on the clock; `watch` tells of the change only.
  let heard = 0;
  const off = p.watch!(() => heard++);
  assert.equal(isPanelAvailable(p, {}), false);
  useRunConsoleStore.setState({ run: RUN });
  assert.deepEqual([isPanelAvailable(p, {}), heard], [false, 0], "a run with nothing timed on screen");
  const t = track();
  assert.deepEqual([isPanelAvailable(p, {}), heard], [true, 1]);
  playbackClock.seek(3);
  assert.equal(heard, 1, "the clock moving is no change");
  t.remove();
  assert.deepEqual([isPanelAvailable(p, {}), heard], [false, 2]);
  off();
  useRunConsoleStore.setState({ run: null });

  // Its flag outlives the run: open with no run to show, it is not drawn.
  assert.equal(classicMounts(ID, { state: { open: true, available: false }, env: {}, proceduralActive: false }), false);
  assert.equal(classicMounts(ID, { state: { open: true, available: true }, env: {}, proceduralActive: false }), true);
});

test("it starts in a window over the top right of the view, once, and then stays where the user puts it", () => {
  freshLayout();
  const p = panel();
  assert.equal(P.homeOf(p), "column");
  assert.ok(P.startsFloated(p) && P.canFloat(p) && P.canDock(p) && p.capabilities.stackable);
  assert.equal(P.placeOf(P.classicDoc(), ID), "home");

  // Given its window although no run is on screen yet: it shows there once it is opened.
  P.adoptDockedPanels();
  const f = P.classicDoc().floats[ID];
  assert.ok(f, "given a window of the layout");
  // 440 wide, 12 px in from the right edge, 56 px down, no taller than 62 % of the page.
  assert.deepEqual([f.x, f.y, f.w, f.h], [1280 - 440 - 12, 56, 440, 480]);
  assert.ok(f.h <= 0.62 * 800);
  assert.deepEqual(floatActions(p, P.classicDoc()).map((a) => a.id), ["dock", "minimise", "move"]);

  // Moved by the user: adopting again leaves it there.
  P.updateClassic((d) => setFloatRect(d, ID, { x: 100, y: 200, w: 500, h: 300 }));
  P.adoptDockedPanels();
  assert.deepEqual([P.classicDoc().floats[ID].x, P.classicDoc().floats[ID].w], [100, 500]);

  // Docked beside the view, then stacked with another panel's group.
  P.dockAt(ID, { node: VIEWPORT_NODE, edge: "right" });
  P.adoptDockedPanels();
  assert.equal(P.placeOf(P.classicDoc(), ID), "grid");
  P.dockAt(CORE_PANEL_IDS.scene, { node: groupOf(gridOf(P.classicDoc()), ID)!.id, edge: "center" });
  assert.deepEqual(groupOf(gridOf(P.classicDoc()), ID)!.panels, [ID, CORE_PANEL_IDS.scene]);

  // Back in the panel column: it stays there, with Float and Move in its header.
  P.backHome(ID);
  P.adoptDockedPanels();
  assert.equal(P.placeOf(P.classicDoc(), ID), "home");
  assert.deepEqual(columnActions(p, P.classicDoc()).map((a) => a.id), ["float", "move"]);
  // Floated again by the user: where it started, not in the cascade of the other windows.
  P.floatOut(ID);
  assert.deepEqual([P.classicDoc().floats[ID].x, P.classicDoc().floats[ID].y], [1280 - 440 - 12, 56]);
  freshLayout();
});

test("a short page gets a shorter window; an existing arrangement is given the window too", () => {
  freshLayout();
  page.window.innerHeight = 500;
  try {
    // An arrangement saved before the console was a panel: it does not know the panel yet.
    P.updateClassic((d) => ({ ...d, known: [CORE_PANEL_IDS.scene] }));
    P.adoptDockedPanels();
    const f = P.classicDoc().floats[ID];
    assert.equal(f.h, 310, "62 % of the page's height");
    assert.ok(P.classicDoc().known.includes(ID));
  } finally {
    page.window.innerHeight = 800;
    freshLayout();
  }
});

test("with panels not customisable, or below `wide`, it shows in the panel column", () => {
  freshLayout();
  P.adoptDockedPanels();
  const doc = P.classicDoc();
  assert.equal(P.shownPlace(doc, ID, true, false), "float");
  assert.equal(P.shownPlace(doc, ID, false, false), "home", "the setting off: every panel is at home");
  assert.equal(P.shownPlace(doc, ID, true, true), "home", "a window takes the whole screen there: it starts in one, it was not put in one");
  // A panel the user floated keeps its window there, as before.
  P.floatOut(CORE_PANEL_IDS.scene);
  assert.equal(P.shownPlace(P.classicDoc(), CORE_PANEL_IDS.scene, true, true), "float");
  freshLayout();
});

/** The transport's element tree, its hooks run by a component of the test's. */
function transport(): { html: string; console: React.ReactElement<{ onClick(): void; "aria-pressed": boolean }> | null } {
  let tree: React.ReactNode = null;
  const Probe: React.FC = () => (tree = (PlaybackTransport as unknown as (props: object) => React.ReactNode)({})) as React.ReactElement;
  const html = renderToStaticMarkup(<Probe />);
  const find = (node: React.ReactNode): React.ReactElement | null => {
    if (Array.isArray(node)) {
      for (const child of node) {
        const hit = find(child);
        if (hit) return hit;
      }
      return null;
    }
    if (!React.isValidElement(node)) return null;
    const props = node.props as Record<string, unknown>;
    return "data-run-console-toggle" in props ? node : find(props.children as React.ReactNode);
  };
  return { html, console: find(tree) as never };
}

test("the transport's Console button opens and closes the panel through the layout, pressed while it is open", () => {
  useRunConsoleStore.getState().setShown(false);
  const t = track();
  try {
    assert.equal(transport().console, null, "no run on screen: no button");
    setServerState(useRunConsoleStore, { run: RUN });
    let drawn = transport();
    assert.match(drawn.html, /aria-label="Console"[^>]*aria-pressed="false"/);
    assert.doesNotMatch(drawn.html, /data-run-console="true"/, "the console is the layout's to draw, not the transport's");
    drawn.console!.props.onClick();
    assert.deepEqual([useRunConsoleStore.getState().shown, isPanelOpen(ID)], [true, true]);
    drawn = transport();
    assert.match(drawn.html, /aria-label="Console"[^>]*aria-pressed="true"/);
    drawn.console!.props.onClick();
    assert.equal(isPanelOpen(ID), false);
    // Closed from elsewhere (the window's own close button): the button follows.
    toggle(ID);
    hide(ID);
    assert.match(transport().html, /aria-label="Console"[^>]*aria-pressed="false"/);
  } finally {
    t.remove();
    setServerState(useRunConsoleStore, { run: null });
  }
  assert.equal(transport().html, "", "nothing timed on screen: no transport");
});

test("the body draws the run, each actor's state and plan, and the log up to the clock's time", () => {
  const t = track();
  try {
    setServerState(useRunConsoleStore, { run: RUN, feed: feed(), live: true, error: null });
    playbackClock.seek(5);
    const html = renderToStaticMarkup(<RunConsoleBody />);
    assert.match(html, /data-run-console="true"/);
    assert.match(html, /title="Run one"/);
    assert.match(html, />running</);
    assert.match(html, /data-console-actor="a"[\s\S]*First[\s\S]*Moving to the start[\s\S]*Wait for the other/);
    assert.match(html, /data-console-actor="b"/);
    assert.match(html, /data-console-log[\s\S]*Nothing arrived/);
    assert.doesNotMatch(html, /Done/, "what was said after the clock's time is not in it yet");
    // No frame of its own: the host has the title bar and the close button.
    assert.doesNotMatch(html, /Hide the console/);
    assert.match(renderToStaticMarkup(<RunConsoleBody />), / p-2"/, "it pads itself where it fills its host");
    assert.doesNotMatch(renderToStaticMarkup(<RunConsoleBody bare />), / p-2"/);

    // A run that has said nothing yet.
    setServerState(useRunConsoleStore, { feed: null });
    assert.match(renderToStaticMarkup(<RunConsoleBody />), /Waiting for the run to say what it is doing/);
    setServerState(useRunConsoleStore, { feed: feed() });

    // In the panel column: the same in a card with its name and a button that hides it.
    setServerState(useRunConsoleStore, { shown: true });
    const card = renderToStaticMarkup(<RunConsole />);
    assert.match(card, /data-run-console-card="true"/);
    assert.match(card, /aria-label="Hide the console"/);
    assert.match(card, /Moving to the start/);
    setServerState(useRunConsoleStore, { shown: false });
    assert.equal(renderToStaticMarkup(<RunConsole />), "");
  } finally {
    t.remove();
    setServerState(useRunConsoleStore, { run: null, feed: null, live: false });
  }
});
