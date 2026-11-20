// A clash contact clicked in the 3D view (its marker or patch) is described in the Selected Object
// panel -- its joint type, the members in contact, where it is, the specs that claim it -- with a
// link that opens it in Connections > Review; the click itself opens Review and the Selected Object
// panel, and marks the contact as the one the list opens at (`clashContactPickStore`), so the list
// opens its detail even when it was the cursor already. An ordinary pick of the model ends it.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { parseClashResult, useClashCheckStore } = await import("@/state/clashCheckStore");
const { useClashContactPickStore } = await import("@/state/clashContactPickStore");
const { JOINTS_REVIEW_TAB, CLASHES_TAB, useConnectionBuilderTabStore } = await import("@/state/connectionBuilderTabStore");
const { default: ClashContactSection, showContactInConnections } = await import(
  "@/components/info_box_selected_object/ClashContactSection"
);
const { CORE_PROPERTY_PROVIDERS } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
const { focusJoint } = await import("@/utils/scene/clashJointFocus");

const TYPE = "2|BEAM:I:GIRDER+BEAM:I:COLUMN|perpendicular";
const RESULT = parseClashResult({
  schema: "ada.clash/result@1",
  source_key: "models/a.ifc",
  source_sha256: "abc",
  options: {},
  counts: { members: 2, beams: 2, plates: 0, joints: 1, joints_with_a_generator: 1 },
  joints: [
    {
      id: "j1abcdef99",
      centre: [1, 2, 3],
      members: [
        { name: "bm1", kind: "BEAM", section: "IPE300", member_type: "GIRDER" },
        { name: "col1", kind: "BEAM", section: "HEB300", member_type: "COLUMN" },
      ],
      type_key: TYPE,
      type_label: "2 × BEAM · I · Girder→Column · perpendicular",
      applicable: [{ spec: "builtin.girder_gusset", priority: 10 }],
    },
  ],
  groups: [{ type_key: TYPE, type_label: "girder to column", count: 1, joint_ids: ["j1abcdef99"], applicable: [] }],
});

const render = () => {
  setServerState(useClashCheckStore, { result: useClashCheckStore.getState().result });
  setServerState(useClashContactPickStore, { jointId: useClashContactPickStore.getState().jointId });
  return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(ClashContactSection)));
};

beforeEach(() => {
  useClashCheckStore.setState({ result: RESULT, selectedJoint: null });
  useClashContactPickStore.getState().clear();
  useConnectionBuilderTabStore.getState().select(CLASHES_TAB);
});

test("nothing is shown while no contact is picked", () => {
  assert.equal(render(), "");
  assert.ok(CORE_PROPERTY_PROVIDERS.some((p) => p.id === "clash-contact"), "a section of the Selected Object panel");
});

test("a picked contact shows its type, members, position and specs, with a link to Connections", () => {
  useClashContactPickStore.getState().pick("j1abcdef99");
  const html = render();
  assert.match(html, /Contact/);
  assert.match(html, /Girder→Column/);
  assert.match(html, /bm1/);
  assert.match(html, /HEB300/);
  assert.match(html, /1\.00, 2\.00, 3\.00/);
  assert.match(html, /builtin\.girder_gusset/);
  assert.match(html, /Open in Connections/);
});

test("the link opens Review at the contact; a click in 3D picks it, and each request is a new one", async () => {
  showContactInConnections("j1abcdef99");
  assert.equal(useConnectionBuilderTabStore.getState().tab, JOINTS_REVIEW_TAB);
  assert.equal(useClashCheckStore.getState().selectedJoint, "j1abcdef99");
  const seq = useClashContactPickStore.getState().seq;

  useConnectionBuilderTabStore.getState().select(CLASHES_TAB);
  await focusJoint("j1abcdef99", { revealPanel: true });
  assert.equal(useConnectionBuilderTabStore.getState().tab, JOINTS_REVIEW_TAB);
  assert.equal(useClashContactPickStore.getState().jointId, "j1abcdef99");
  assert.ok(useClashContactPickStore.getState().seq > seq, "the same contact again is a new request");

  // Focused from a panel (not the 3D view): no pick.
  useClashContactPickStore.getState().clear();
  await focusJoint("j1abcdef99");
  assert.equal(useClashContactPickStore.getState().jointId, null);
});

test("the list opens at a picked contact, its detail open, and an ordinary pick ends it", async () => {
  const { readFile } = await import("node:fs/promises");
  const list = await readFile(new URL("../../components/info_box_scene/joints/IdentifiedJoints.tsx", import.meta.url), "utf8");
  assert.match(list, /useClashContactPickStore\(\(s\) => s\.seq\)/);
  assert.match(list, /setCursorId\(`j:\$\{focusedJoint\}`\);\s*setDetailOpen\(true\);/);
  for (const rel of ["../../components/viewer/sceneHelpers/setupPointerHandler.ts", "../../components/viewer/sceneHelpers/viewportPanelBridge.ts"]) {
    const src = await readFile(new URL(rel, import.meta.url), "utf8");
    assert.match(src, /useClashContactPickStore\.getState\(\)\.clear\(\)/, rel);
  }
});
