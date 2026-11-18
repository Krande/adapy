// Controls profiles: Default (the viewer's own controls) and GeniE (DNV GeniE's, from its user
// documentation) built in; the user's own made from the settings in force, edited by changing the
// controls while chosen, renamed, deleted, exported and imported. Choosing one applies its camera mapping
// and, when it holds them, its keys.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { CAMERA_PRESETS, DEFAULT_CAMERA_MAPPING, cloneMapping, dragAction, presetOf } = await import("@/utils/camera/cameraMapping");
const {
  BUILT_IN_PROFILES, GENIE_KEYS, GENIE_PROFILE_ID, DEFAULT_PROFILE_ID, exportProfiles, importProfiles, profileMatches, uniqueProfileName,
} = await import("@/utils/camera/controlProfiles");
const { useCameraControlsStore, migrateStoredMapping } = await import("@/state/cameraControlsStore");
const { useControlsProfileStore, allProfiles, currentKeys } = await import("@/state/controlsProfileStore");
const { layoutStore } = await import("@/layout/layoutStore");
const { refuseChord, normaliseChord } = await import("@/layout/keyBindings");
const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { pointerGestures } = await import("@/components/options/ShortcutsModal");

function reset() {
  useControlsProfileStore.setState({ custom: [], activeId: DEFAULT_PROFILE_ID });
  useCameraControlsStore.getState().setMapping(cloneMapping(DEFAULT_CAMERA_MAPPING));
  layoutStore.getState().resetKeyOverrides();
}

test("GeniE's mouse, as its user documentation has it (2.2.4, 2.2.5, 3.3.3)", () => {
  const m = CAMERA_PRESETS.genie.mapping;
  // Right: rotate; Shift + right: zoom; Ctrl + right: pan.
  assert.equal(dragAction(m, "right"), "orbit");
  assert.equal(dragAction(m, "right", { shiftKey: true }), "zoom");
  assert.equal(dragAction(m, "right", { ctrlKey: true }), "pan");
  // Ctrl + Shift + right (GeniE's zoom area) counts as Shift: a zoom, not a zoom to a rectangle.
  assert.equal(dragAction(m, "right", { ctrlKey: true, shiftKey: true }), "zoom");
  // Left only selects: a drag is the rubber band, with or without Shift.
  for (const mods of [{}, { shiftKey: true }, { ctrlKey: true }]) assert.equal(dragAction(m, "left", mods), "select");
  // The middle button is not used; the wheel zooms, about the pointer.
  assert.equal(dragAction(m, "middle"), "none");
  assert.deepEqual(m.wheel, { zoom: true, inverted: false, toCursor: true });
  assert.equal(presetOf(m), "genie");
});

test("GeniE's keys are ones a page gets, and take no key another core shortcut or a reserved widget key keeps", () => {
  const overridden = new Set(Object.keys(GENIE_KEYS));
  for (const [id, keys] of Object.entries(GENIE_KEYS)) {
    assert.ok(CORE_SHORTCUTS.some((s) => s.id === id), `${id} is a core shortcut`);
    assert.equal(refuseChord(keys!), null, `${keys} can be assigned`);
    // A widget's own key counts when it reserves it (F6 moves the focus between the viewer's areas).
    const clash = CORE_SHORTCUTS.find((s) => (!s.local || s.reserves) && !overridden.has(s.id) && normaliseChord(s.keys) === normaliseChord(keys!));
    assert.equal(clash, undefined, `${keys} is free`);
  }
  // F5 (GeniE's isometric view) reloads the page, F6 (its view from X) is the viewer's focus key: not in the profile.
  assert.ok(refuseChord("F5"));
  assert.ok(!Object.values(GENIE_KEYS).includes("F5") && !Object.values(GENIE_KEYS).includes("F6"));
});

test("choosing GeniE applies its mouse and its keys; choosing Default puts both back", () => {
  reset();
  useControlsProfileStore.getState().choose(GENIE_PROFILE_ID);
  assert.equal(presetOf(useCameraControlsStore.getState().mapping), "genie");
  assert.deepEqual(currentKeys(), GENIE_KEYS);
  assert.equal(useControlsProfileStore.getState().activeId, GENIE_PROFILE_ID);
  useControlsProfileStore.getState().choose(DEFAULT_PROFILE_ID);
  assert.equal(presetOf(useCameraControlsStore.getState().mapping), "default");
  assert.deepEqual(currentKeys(), {});
});

test("a built-in profile is never changed: edited, it reads as modified", () => {
  reset();
  useControlsProfileStore.getState().choose(GENIE_PROFILE_ID);
  useCameraControlsStore.getState().setWheelInverted(true);
  const genie = BUILT_IN_PROFILES.find((p) => p.id === GENIE_PROFILE_ID)!;
  assert.equal(genie.mapping.wheel.inverted, false);
  assert.ok(!profileMatches(genie, useCameraControlsStore.getState().mapping, currentKeys()));
});

test("my profile: made from the settings in force, it follows changes while chosen; renamed, deleted", () => {
  reset();
  const store = () => useControlsProfileStore.getState();
  store().choose(GENIE_PROFILE_ID);
  const id = store().createFromCurrent("  Site   desk  ", true);
  const mine = () => allProfiles().find((p) => p.id === id)!;
  assert.equal(mine().name, "Site desk");
  assert.equal(store().activeId, id);
  assert.deepEqual(mine().keys, GENIE_KEYS);
  // Edited by changing the controls (and the keys, which it holds).
  useCameraControlsStore.getState().setDragAction("middle", "none", "pan");
  layoutStore.getState().setKeyOverride("core:fit-all", "F2");
  assert.equal(mine().mapping.drag.middle.none, "pan");
  assert.equal(mine().keys!["core:fit-all"], "F2");
  // A profile without keys does not follow them.
  const noKeys = store().createFromCurrent("Mouse only", false);
  layoutStore.getState().setKeyOverride("core:fit-all", "F9");
  assert.equal(allProfiles().find((p) => p.id === noKeys)!.keys, undefined);
  // Names are unique, ignoring case.
  assert.equal(uniqueProfileName("geniE", allProfiles().map((p) => p.name)), "geniE (2)");
  assert.match(store().rename(id, "mouse ONLY") ?? "", /already/);
  assert.match(store().rename(GENIE_PROFILE_ID, "Mine") ?? "", /own/);
  assert.equal(store().rename(id, "Desk"), null);
  assert.equal(mine().name, "Desk");
  // Deleting the chosen profile chooses Default, and applies it.
  store().choose(id);
  store().remove(id);
  assert.equal(store().activeId, DEFAULT_PROFILE_ID);
  assert.equal(presetOf(useCameraControlsStore.getState().mapping), "default");
  assert.deepEqual(currentKeys(), {});
  reset();
});

test("export and import: names, mappings and keys travel; a bad file says why", () => {
  reset();
  const id = useControlsProfileStore.getState().createFromCurrent("Desk", true);
  const text = exportProfiles(allProfiles().filter((p) => p.id === id));
  const data = JSON.parse(text);
  assert.equal(data.format, "ada-controls-profiles");
  assert.equal(data.profiles[0].id, undefined, "ids stay home");
  // Imported under a free name, with a new id.
  assert.equal(useControlsProfileStore.getState().importText(text), 1);
  const names = allProfiles().map((p) => p.name);
  assert.deepEqual(names.slice(-2), ["Desk", "Desk (2)"]);
  assert.notEqual(allProfiles().at(-1)!.id, id);
  // A single exported profile object is accepted too; foreign values are repaired.
  const one = importProfiles(JSON.stringify({ name: "Odd", mapping: { drag: { left: { none: "teleport" } } }, keys: { "core:fit-all": 5, "core:show-all": null } }), () => "x");
  assert.equal(one[0].mapping.drag.left.none, DEFAULT_CAMERA_MAPPING.drag.left.none);
  assert.deepEqual(one[0].keys, { "core:show-all": null });
  assert.throws(() => importProfiles("{", () => "x"), /not JSON/);
  assert.throws(() => importProfiles("[1, 2]", () => "x"), /no controls profiles/);
  assert.throws(() => importProfiles(JSON.stringify({ profiles: [{ mapping: {} }] }), () => "x"), /name/);
  reset();
});

test("the chosen profile and my profiles are kept in the browser, repaired when read back", async () => {
  reset();
  const id = useControlsProfileStore.getState().createFromCurrent("Kept", false);
  const stored = JSON.parse(window.localStorage.getItem("ada-controls-profiles")!);
  assert.equal(stored.state.activeId, id);
  assert.equal(stored.state.custom[0].name, "Kept");
  // Read back: an unknown active id falls back to Default, a profile without a name is dropped.
  window.localStorage.setItem("ada-controls-profiles", JSON.stringify({ state: { activeId: "gone", custom: [{ name: "" }, stored.state.custom[0]] }, version: 1 }));
  await useControlsProfileStore.persist.rehydrate();
  assert.equal(useControlsProfileStore.getState().activeId, DEFAULT_PROFILE_ID);
  assert.deepEqual(useControlsProfileStore.getState().custom.map((p) => p.name), ["Kept"]);
  reset();
});

test("a mapping stored before the rectangle was in it keeps Shift + left drawing it", () => {
  const old = cloneMapping(DEFAULT_CAMERA_MAPPING);
  old.drag.left.shift = "pan"; // what version 1 stored; the rectangle took the drag all the same
  assert.equal(migrateStoredMapping(old, 1).mapping.drag.left.shift, "select");
  const now = cloneMapping(DEFAULT_CAMERA_MAPPING);
  now.drag.left.shift = "orbit";
  assert.equal(migrateStoredMapping(now, 2).mapping.drag.left.shift, "orbit", "a version-2 choice is kept");
});

test("the shortcuts dialog's mouse section follows the profile", () => {
  const keysOf = (m: Parameters<typeof pointerGestures>[0]) => pointerGestures(m).shortcuts.map((s) => s.keys.join(" + "));
  assert.ok(keysOf(DEFAULT_CAMERA_MAPPING).includes("Shift + Left-drag"));
  const genie = keysOf(CAMERA_PRESETS.genie.mapping);
  assert.ok(genie.includes("Left-drag") && genie.includes("Shift + Right-drag") && !genie.some((k) => k.includes("Middle")));
  const select = pointerGestures(CAMERA_PRESETS.genie.mapping).shortcuts.find((s) => s.keys.join(" + ") === "Left-drag")!;
  assert.match(select.description, /Box select/);
});
