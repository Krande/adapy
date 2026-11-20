// A picked weld's Properties: its type (symbol and name), throat, length and the members it joins,
// and the joint it is a weld of, with a "Show joint" link that lists the joint in Connections >
// Review. The joints come from each loaded source's GLB (`connectionGraphStore.register`), by either
// viewport, and go with their source.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { default: WeldRows, isWeldMeta, showJoint } = await import("@/components/info_box_selected_object/WeldRows");
const { default: JointsOverviewPanel } = await import("@/components/info_box_scene/JointsOverviewPanel");
const { connectionOfWeld, useConnectionGraphStore } = await import("@/state/connectionGraphStore");
const { useJointFocusStore } = await import("@/state/jointFocusStore");
const { useModelState } = await import("@/state/modelState");
const { JOINTS_REVIEW_TAB, useConnectionBuilderTabStore } = await import("@/state/connectionBuilderTabStore");

const FILE = "detail.glb";
const EXT = {
  design_objects: [
    {
      connections: [
        {
          name: "spec.x_j1",
          spec_name: "spec.x",
          member_roles: { landing: ["col"], incoming: ["beam"] },
          beam_names: null,
          plate_names: null,
          weld_names: ["weld2681"],
        },
      ],
    },
  ],
};
const WELD = {
  type: "weld" as const,
  weld_type: "FILLET",
  throat: 0.006,
  members: ["beam", "col"],
  length: 0.25,
  at: [0, 0, 0],
};

/** Server-rendered with the stores as they are now (a server render reads a store's initial state). */
const within = (node: React.ReactElement) => {
  const graph = useConnectionGraphStore.getState();
  setServerState(useConnectionGraphStore, { indexByMember: graph.indexByMember, byFile: graph.byFile });
  setServerState(useJointFocusStore, { joints: useJointFocusStore.getState().joints });
  return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, node));
};

beforeEach(() => {
  useConnectionGraphStore.getState().clearIndex();
  useJointFocusStore.getState().clear();
  useModelState.setState({ loadedSourceNames: new Set([FILE]) });
});

test("a weld's metadata is recognised whatever the case of its type", () => {
  assert.ok(isWeldMeta(WELD) && isWeldMeta({ type: "Weld" }));
  assert.ok(!isWeldMeta({ type: "Beam" }) && !isWeldMeta(null));
});

test("a weld shows its type, throat, length, members and joint", () => {
  useConnectionGraphStore.getState().register(FILE, EXT);
  const html = within(React.createElement(WeldRows, { meta: WELD, fileName: FILE, weldName: "weld2681" }));
  assert.match(html, /<svg/, "the ISO 2553 symbol");
  assert.match(html, /Weld · fillet/);
  assert.match(html, /Fillet weld/, "the symbol's description");
  assert.match(html, /a = 6 mm/);
  assert.match(html, /250 mm/);
  assert.match(html, /beam<span[^>]*> \(incoming\)/);
  assert.match(html, /col<span[^>]*> \(landing\)/);
  assert.match(html, /spec\.x_j1/);
  assert.match(html, /Show joint/);
  assert.doesNotMatch(html, /No metadata available/);
});

test("without a connection listing it, a weld shows no joint row", () => {
  const html = within(React.createElement(WeldRows, { meta: { type: "weld" }, fileName: FILE, weldName: "w" }));
  assert.match(html, /Weld · weld/);
  assert.doesNotMatch(html, /Joint:|Length:/);
});

test("Show joint lists the joint in Review and opens that tab", () => {
  useConnectionGraphStore.getState().register(FILE, EXT);
  const conn = connectionOfWeld(useConnectionGraphStore.getState().indexByMember, FILE, "weld2681")!;
  assert.equal(conn.file, FILE);
  showJoint(FILE, conn);
  assert.equal(useConnectionBuilderTabStore.getState().tab, JOINTS_REVIEW_TAB);
  assert.deepEqual(useJointFocusStore.getState().joints.map((j) => j.name), ["spec.x_j1"]);
  const html = within(React.createElement(JointsOverviewPanel));
  assert.match(html, /data-focused-joint="spec\.x_j1"/);
  assert.match(html, /weld2681/);
  assert.match(html, /landing/);
});

test("each source keeps its own joints, and a source that leaves takes its joints with it", () => {
  const store = useConnectionGraphStore.getState();
  store.register(FILE, EXT);
  store.register("model.glb", { design_objects: [{ connections: [{ name: "other", beam_names: ["beam"] }] }] });
  useModelState.setState({ loadedSourceNames: new Set([FILE, "model.glb"]) });
  assert.deepEqual(useConnectionGraphStore.getState().indexByMember.get("beam")?.map((c) => c.name), ["other"]);
  assert.ok(useConnectionGraphStore.getState().indexByMember.get("weld2681"), "the first source's joints are kept");
  useJointFocusStore.getState().focus(FILE, "spec.x_j1");
  useModelState.setState({ loadedSourceNames: new Set(["model.glb"]) });
  assert.equal(useConnectionGraphStore.getState().indexByMember.get("weld2681"), undefined);
  assert.deepEqual(useJointFocusStore.getState().joints, []);
});
