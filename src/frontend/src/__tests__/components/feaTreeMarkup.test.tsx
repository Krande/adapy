// The FEA rows as the Scene tree draws them (informational and isolated rows), their row menu,
// the Selected Object panel's FEA section, and the Desktop UI's FEA menu.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { CustomNode } = await import("@/components/tree_view/CustomNode");
const { feaRowMenuItems, isFeaMenu } = await import("@/components/tree_view/feaRowMenu");
const { FeaSetsSection } = await import("@/components/info_box_selected_object/FeaSetsSection");
const { useFeaIsolationStore } = await import("@/state/feaIsolationStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { useObjectInfoStore } = await import("@/state/objectInfoStore");
const { buildSetsRoot, elementSetRowId, nodeSetRowId, propertyValueRowId } = await import("@/utils/scene/fea/feaTreeRows");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { MIRRORED_CORE_COMMANDS, SCENE_ACTION_COMMANDS } = await import("@/layout/sceneActions");

type Row = import("@/components/tree_view/CustomNode").TreeNodeData;
type View = import("@/state/feaIsolationStore").FeaTreeView;

function renderRow(data: Row): string {
  const node = { data, isSelected: false, isOpen: false, children: [], level: 2, tree: { props: { data: [] } }, toggle() {} };
  return renderToStaticMarkup(React.createElement(CustomNode, { node, style: {}, dragHandle: undefined } as never));
}

const SETS = [
  { name: "Deck", members: ["EL1", "EL2"], fe_object_type: "element" as const },
  { name: "Corners", members: ["P1"], fe_object_type: "node" as const },
];

const view = (over: Partial<View> = {}): View => ({
  loaded: true,
  roots: [],
  isolation: { rows: [], contextWireframe: true },
  members: 0,
  contextReason: null,
  isolationReason: null,
  picked: null,
  ...over,
});

test("an isolated set row is marked, a node set row is informational with its reason", () => {
  const root = buildSetsRoot(SETS, new Set([elementSetRowId("Deck")]))!;
  const [elements, nodes] = root.children;
  const deck = renderRow(elements.children[0]);
  assert.match(deck, /data-marked="true"/);
  assert.match(deck, /aria-label="isolated"/);
  assert.match(deck, />2 el</);
  const corners = renderRow(nodes.children[0]);
  assert.match(corners, /data-informational="true"/);
  assert.match(corners, /cannot isolate elements/);
  assert.doesNotMatch(corners, /data-marked/);
});

test("the row menu isolates, adds, removes, clears and hides through the scene actions", () => {
  const calls: string[] = [];
  const actions = {
    feaIsolate: (ids: readonly string[], mode?: string) => void calls.push(`isolate ${mode} ${ids.join(",")}`),
    feaClearIsolation: () => void calls.push("clear"),
    feaToggleContext: () => void calls.push("context"),
    feaHideRows: (ids: readonly string[]) => void calls.push(`hide ${ids.join(",")}`),
    feaShowRows: (ids: readonly string[]) => void calls.push(`show ${ids.join(",")}`),
  };
  const deck = { id: elementSetRowId("Deck"), name: "Deck", children: [] };
  const corners = { id: nodeSetRowId("Corners"), name: "Corners", children: [] };
  assert.ok(isFeaMenu([deck, corners]));
  assert.ok(!isFeaMenu([deck, { id: "E1", name: "E1", children: [] }]), "a model row keeps the model menu");

  const fresh = feaRowMenuItems([deck], view(), actions);
  const by = (items: typeof fresh, key: string) => items.find((i) => i.key === key)!;
  assert.equal(by(fresh, "fea-isolate").disabled, false);
  assert.equal(by(fresh, "fea-isolate-add").disabled, true, "nothing isolated to add to");
  assert.equal(by(fresh, "fea-clear").disabled, true);
  assert.equal(by(fresh, "fea-context").label, "Rest as wireframe: on");
  by(fresh, "fea-isolate").onClick();
  by(fresh, "fea-hide").onClick();

  const other = propertyValueRowId("props.material", 1);
  const isolated = feaRowMenuItems([deck], view({ isolation: { rows: [other], contextWireframe: false } }), actions);
  assert.equal(by(isolated, "fea-isolate-add").disabled, false);
  assert.equal(by(isolated, "fea-isolate-remove").disabled, true);
  assert.equal(by(isolated, "fea-context").label, "Rest as wireframe: off");
  by(isolated, "fea-isolate-add").onClick();
  by(isolated, "fea-clear").onClick();
  by(isolated, "fea-context").onClick();

  const nodeOnly = feaRowMenuItems([corners], view({ contextReason: "This result carries no element edges to draw the rest with" }), actions);
  assert.equal(by(nodeOnly, "fea-isolate").disabled, true);
  assert.match(by(nodeOnly, "fea-isolate").title!, /nodes carry no faces/);
  assert.equal(by(nodeOnly, "fea-context").disabled, true);
  assert.match(by(nodeOnly, "fea-context").title!, /no element edges/);

  assert.deepEqual(calls, [
    `isolate replace ${deck.id}`,
    `hide ${deck.id}`,
    `isolate add ${deck.id}`,
    "clear",
    "context",
  ]);
});

test("the Selected Object panel's FEA section lists the pick's values and sets, and nothing without a pick", () => {
  const render = () => renderToStaticMarkup(React.createElement(FeaSetsSection));
  assert.equal(render(), "");
  const material = {
    name_canonical: "props.material",
    category: "property",
    components: ["value"],
    value_labels: { "1": "Steel" },
    per_type: [{ elem_type: "quad", n_elements: 2, n_ips: 1 }],
  };
  useFeaAnimationStore.setState({ sourceName: "r.rmed", manifest: { mesh: {}, fields: [material] } as never });
  const s = useFeaIsolationStore.getState();
  s.reset("r.rmed");
  s.setSets("r.rmed", SETS);
  useFeaIsolationStore.setState({ membership: new Map([["props.material", new Map([["E2", 1]])]]) });
  useObjectInfoStore.setState({ name: "E2" });
  try {
    const html = render();
    assert.match(html, /FEA sets and properties/);
    assert.match(html, /Material:/);
    assert.match(html, />Steel</);
    assert.match(html, /In 1 set:/);
    assert.match(html, />Deck</);
    assert.equal((html.match(/>Isolate</g) ?? []).length, 2);
    useObjectInfoStore.setState({ name: "E9" });
    assert.equal(render(), "", "an element no set or property names");
  } finally {
    useObjectInfoStore.setState({ name: null });
    useFeaAnimationStore.setState({ sourceName: null, manifest: null });
    useFeaIsolationStore.getState().reset(null);
  }
});

test("the Desktop UI's View menu has an FEA sets submenu of core's commands, and pop-outs reach them", () => {
  const menus = desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] });
  const viewMenu = menus.find((m) => m.label === "View")!;
  const fea = viewMenu.items.find((i) => typeof i === "object" && "label" in i && i.label === "FEA sets") as { items: unknown[] } | undefined;
  assert.ok(fea, "View -> FEA sets");
  const ids = menuCommandIds([viewMenu]);
  for (const id of [ID.feaIsolateSets, ID.feaIsolateSelectionSets, ID.feaClearIsolation, ID.feaToggleContextWireframe]) assert.ok(ids.includes(id), id);
  // The commands act through the scene actions, whose mirror commands a pop-out sends.
  assert.ok(Object.values(SCENE_ACTION_COMMANDS).includes("core:fea-isolate"));
  assert.ok(!MIRRORED_CORE_COMMANDS.includes(ID.feaClearIsolation), "not through core:run-command");
});
