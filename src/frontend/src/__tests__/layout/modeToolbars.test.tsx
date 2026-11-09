// Core's mode toolbars: the commands behind them, the toolbars' markup, the
// `toolbars` a mode names, and the Desktop UI's use of them.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("@/layout/commands");
const M = await import("@/layout/shellModes");
const D = await import("@/layout/desktop/desktopShell");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { CORE_COMMAND_ICONS } = await import("@/layout/commandIcons");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const T = await import("@/layout/ModeToolbars");
const { ShellModeToolbar } = await import("@/layout/ShellModeSwitcher");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useSectionStore } = await import("@/state/sectionStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { useModelState } = await import("@/state/modelState");
const P = await import("@/utils/scene/fea/playbackActions");
const THREE = await import("three");
const V = await import("@/viewer-core");
const { readFileSync } = await import("node:fs");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
const run = (id: string) => assert.ok(C.runCommand(id), `${id} ran`);

function setup(): void {
  C.resetCommands();
  registerCoreCommands();
  useSectionStore.getState().clearAll();
  useFeaAnimationStore.getState().reset();
}

const NEW_COMMANDS = [
  ID.playPause, ID.stopResults, ID.stepBack, ID.stepForward,
  ID.sectionX, ID.sectionY, ID.sectionZ, ID.sectionToggle, ID.sectionFlip, ID.sectionClear,
  ID.feaToggleUndeformed, ID.feaToggleElementEdges, ID.feaToggleResultColors,
];

test("every toolbar command is registered, with an icon", () => {
  setup();
  for (const id of NEW_COMMANDS) {
    assert.ok(C.getCommand(id), `${id} is registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon`);
  }
  C.resetCommands();
});

test("playback commands are refused without a result, and drive the store with one", async () => {
  setup();
  for (const id of [ID.playPause, ID.stopResults, ID.stepBack, ID.stepForward]) {
    assert.equal(C.getCommand(id)!.disabledReason?.(), "No result is loaded");
  }
  const applied: number[] = [];
  const s = useFeaAnimationStore.getState();
  s.setSessionActive(true);
  s.setNSteps(3);
  s.setApplyStep(async (i) => void applied.push(i));
  assert.equal(C.getCommand(ID.playPause)!.disabledReason?.(), null);

  P.stepResults(1);
  assert.equal(useFeaAnimationStore.getState().stepIndex, 1);
  P.stepResults(-2); // wraps round the start
  assert.equal(useFeaAnimationStore.getState().stepIndex, 2);
  P.stepResults(1); // and the end
  assert.equal(useFeaAnimationStore.getState().stepIndex, 0);
  assert.deepEqual(applied, [1, 2, 0]);

  P.setResultStep(99); // clamped
  assert.equal(useFeaAnimationStore.getState().stepIndex, 2);

  P.togglePlayResults();
  assert.equal(useFeaAnimationStore.getState().isPlaying, true);
  assert.equal(C.getCommand(ID.playPause)!.checked?.(), true);
  P.togglePlayResults();
  assert.equal(useFeaAnimationStore.getState().isPlaying, false);

  P.setResultScale(NaN); // an edit in progress changes nothing
  P.setResultScale(5);
  assert.equal(useFeaAnimationStore.getState().scaleFactor, 5);
  assert.equal(useFeaAnimationStore.getState().scaleFactorAuto, false);

  P.stopResults();
  assert.equal(useFeaAnimationStore.getState().factor, 0);
  assert.equal(useFeaAnimationStore.getState().isPlaying, false);
  // The command runs the same action once its module has loaded.
  run(ID.stepForward);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(useFeaAnimationStore.getState().stepIndex, 0, "the step after the last wraps to the first");
  C.resetCommands();
});

test("section commands add, switch, flip and clear the plane the toolbar edits", () => {
  setup();
  const planes = () => useSectionStore.getState().planes;
  assert.equal(C.getCommand(ID.sectionFlip)!.disabledReason?.(), "No section plane");
  run(ID.sectionX);
  assert.equal(planes().length, 1);
  assert.deepEqual(planes()[0].normal, [1, 0, 0]);
  run(ID.sectionToggle);
  assert.equal(planes()[0].enabled, false);
  assert.equal(C.getCommand(ID.sectionToggle)!.checked?.(), false);
  run(ID.sectionToggle);
  assert.equal(C.getCommand(ID.sectionToggle)!.checked?.(), true);
  run(ID.sectionFlip);
  assert.deepEqual(planes()[0].normal, [-1, -0, -0]);
  run(ID.sectionClear);
  assert.equal(planes().length, 0);
  run(ID.sectionToggle); // with none, the first press adds a Z plane
  assert.deepEqual(planes()[0].normal, [0, 0, 1]);
  useSectionStore.getState().clearAll();
  C.resetCommands();
});

test("representation commands flip the result's view switches, and need a result", () => {
  setup();
  assert.equal(C.getCommand(ID.feaToggleUndeformed)!.disabledReason?.(), "No result is loaded");
  useFeaAnimationStore.getState().setSessionActive(true);
  assert.equal(C.getCommand(ID.feaToggleUndeformed)!.disabledReason?.(), null);
  run(ID.feaToggleUndeformed);
  assert.equal(useFeaAnimationStore.getState().showUndeformed, true);
  assert.equal(C.getCommand(ID.feaToggleUndeformed)!.checked?.(), true);
  run(ID.feaToggleElementEdges);
  assert.equal(useFeaAnimationStore.getState().elementEdgesVisible, false);
  run(ID.feaToggleResultColors);
  assert.equal(useFeaAnimationStore.getState().resultColorsVisible, false);
  C.resetCommands();
});

test("the toolbars render their controls, and a button for a missing command is left out", () => {
  setup();
  // (Server rendering reads a store's initial state, so what depends on a loaded result or a plane is tested below.)
  const playback = html(React.createElement(T.PlaybackToolbar));
  assert.match(playback, /data-mode-toolbar="playback"/);
  assert.match(playback, /data-command="core:results-play-pause"/);
  assert.match(playback, /aria-label="Play results"/);
  const section = html(React.createElement(T.SectionToolbar));
  assert.match(section, /data-mode-toolbar="section"/);
  assert.match(section, /data-command="core:section\.x"/);
  const rep = html(React.createElement(T.RepresentationToolbar));
  assert.match(rep, /data-command="core:edges"/);
  assert.match(rep, /data-command="core:fea-toggle-undeformed"/);

  C.resetCommands(); // nothing registered: no buttons, no throw
  assert.doesNotMatch(html(React.createElement(T.RepresentationToolbar)), /data-command/);
});

test("the offset slider spans the model along the plane's axis, either way it faces", () => {
  const box = new THREE.Box3(new THREE.Vector3(0, -5, 10), new THREE.Vector3(10, 5, 30));
  assert.deepEqual(T.sectionOffset(undefined, box), { axis: 2, sign: 1, offset: 0, lo: 10, hi: 30 });
  assert.deepEqual(T.sectionOffset({ normal: [0, 1, 0], constant: -2 }, box), { axis: 1, sign: 1, offset: 2, lo: -5, hi: 5 });
  // Flipped: normal -Y, constant +2 is the same plane at y = 2.
  assert.deepEqual(T.sectionOffset({ normal: [0, -1, 0], constant: 2 }, box), { axis: 1, sign: -1, offset: 2, lo: -5, hi: 5 });
  assert.deepEqual(T.sectionOffset(undefined, null), { axis: 2, sign: 1, offset: 0, lo: -1, hi: 1 });
});
test("a mode names core toolbars; ShellModeToolbar draws them in order, skipping unknown ids", () => {
  setup();
  M.setShellModes([
    { id: "a", label: "A", order: 0, toolbars: ["section", "nope", "playback"] },
    { id: "b", label: "B", order: 1 },
  ], "a");
  const out = html(React.createElement(ShellModeToolbar));
  assert.ok(out.indexOf('data-mode-toolbar="section"') >= 0);
  assert.ok(out.indexOf('data-mode-toolbar="section"') < out.indexOf('data-mode-toolbar="playback"'));
  assert.doesNotMatch(out, /representation/);
  M.setShellMode("b");
  // No toolbars: the region stays (it holds the room), with nothing in it; bare, nothing at all.
  assert.doesNotMatch(html(React.createElement(ShellModeToolbar)), /data-mode-toolbar="/);
  assert.equal(html(React.createElement(ShellModeToolbar, { region: false })), "");
  M.setShellModes([]);
  C.resetCommands();
});

test("the Desktop UI's modes name core toolbars that exist", () => {
  const modes = D.desktopUiShellSpec().layout!.modes!;
  const named = modes.flatMap((m) => m.toolbars ?? []);
  assert.ok(named.includes("playback") && named.includes("section") && named.includes("representation"));
  for (const id of named) assert.ok((M.CORE_MODE_TOOLBAR_IDS as readonly string[]).includes(id), id);
  assert.deepEqual(modes.find((m) => m.id === "results")!.toolbars, ["playback", "representation", "colorBy"]);
});

test("the facade publishes the toolbars, and the API version moved with them", () => {
  assert.equal(V.VIEWER_CORE_API_VERSION, "1.16.0");
  assert.deepEqual([...V.CORE_MODE_TOOLBAR_IDS], ["playback", "section", "representation", "colorBy", "build"]);
  // The layout entry imports every panel (and their styles), so its exports are read from the source.
  const facade = readFileSync(new URL("../../viewer-core/layout.ts", import.meta.url), "utf8");
  for (const name of ["PlaybackToolbar", "SectionToolbar", "RepresentationToolbar", "CoreModeToolbars", "CommandButton"]) {
    assert.match(facade, new RegExp(`\\b${name}\\b[^;]*from "@/layout/ModeToolbars"`), `${name} is exported`);
  }
});
