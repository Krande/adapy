// A detail model's "Not built" report: the joints a run did not build, grouped by their reason with
// the numbers and names taken out, and the failed welds of the joints it did build, per joint.

import assert from "node:assert/strict";
import { test } from "node:test";

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { groupNotBuilt, normaliseReason, parseDetailStats } = await import("@/state/clashCheckStore");
const { NotBuiltReport, JointButton } = await import("@/components/info_box_scene/joints/NotBuiltReport");

const angle = (deg: number) => `takes an incoming at 60 to 120 degrees to the landing; this one is at ${deg}`;

const STATS = parseDetailStats({
  joints: { count: 1, by_type: [], items: [] },
  not_built: [
    { joint_id: "a1", kind: "declined", reason: angle(49.2), declined: [{ spec: "provider.spec", reason: angle(49.2) }] },
    { joint_id: "a2", kind: "declined", reason: angle(131), declined: [{ spec: "provider.spec", reason: angle(131) }] },
    { joint_id: "a3", kind: "declined", reason: angle(12.75), declined: [] },
    { joint_id: "a4", kind: "declined", reason: angle(-3), declined: [] },
    { joint_id: "b1", kind: "failed", spec: "provider.spec", reason: "ValueError: member 'brace_7' has no section", declined: [] },
  ],
  weld_failures: [
    { joint_id: "c1", connection: "conn_c1", place: "flange top", weld_type: "fillet", reason: "the seam runs off the plate" },
    { joint_id: "c1", connection: "conn_c1", place: "w_web", weld_type: "butt", reason: "no geometry: ValueError: degenerate" },
  ],
});

test("reasons that differ only in their numbers and names are one reason", () => {
  assert.equal(normaliseReason(angle(49.2)), normaliseReason(angle(131)));
  assert.equal(normaliseReason("member 'brace_7' has no section"), normaliseReason("member 'column_2' has no section"));
  assert.notEqual(normaliseReason("needs a gap"), normaliseReason("needs cuts"));

  const groups = groupNotBuilt(STATS.notBuilt);
  assert.deepEqual(
    groups.map((g) => [g.kind, g.joints.map((j) => j.jointId)]),
    [
      ["declined", ["a1", "a2", "a3", "a4"]],
      ["failed", ["b1"]],
    ],
  );
  assert.equal(groups[0].example, angle(49.2));
});

test("a decline and a failure with the same sentence are not one group", () => {
  const groups = groupNotBuilt([
    { jointId: "x", kind: "declined", reason: "no room", spec: null, declined: [] },
    { jointId: "y", kind: "failed", reason: "no room", spec: "s", declined: [] },
  ]);
  assert.equal(groups.length, 2);
});

test("the report shows each group with its count and a few joints, and the failed welds per joint", () => {
  const html = renderToStaticMarkup(React.createElement(NotBuiltReport, { stats: STATS, onFocusJoint: () => {} }));
  assert.match(html, /5<\/span> joints not built/);
  // The angle group: its count, three examples and how many more.
  assert.match(html, /takes an incoming at 60 to 120 degrees/);
  assert.match(html, />4<\/span>/);
  for (const id of ["a1", "a2", "a3", "b1"]) assert.match(html, new RegExp(`data-not-built-joint="${id}"`));
  assert.doesNotMatch(html, /data-not-built-joint="a4"/, "a closed group names only a few joints");
  assert.match(html, /and 1 more/);
  assert.match(html, /data-not-built-group="failed"/);
  // The welds that failed, under their joint: place, type, reason.
  assert.match(html, /2<\/span> welds failed/);
  assert.match(html, /data-weld-failures="c1"/);
  assert.match(html, /flange top/);
  assert.match(html, /fillet/);
  assert.match(html, /no geometry: ValueError: degenerate/);
});

test("a joint row focuses its contact", () => {
  const focused: string[] = [];
  const button = JointButton({ id: "a1", selected: false, onFocus: (id: string) => void focused.push(id) }) as unknown as {
    props: { onClick: () => void };
  };
  button.props.onClick();
  assert.deepEqual(focused, ["a1"]);
});

test("a run that lost nothing says so; one from before the records lists its sentences", () => {
  const empty = parseDetailStats({ joints: { count: 1, by_type: [], items: [] }, not_built: [], weld_failures: [] });
  assert.match(renderToStaticMarkup(React.createElement(NotBuiltReport, { stats: empty, onFocusJoint: () => {} })), /Every joint was built/);
  const legacy = parseDetailStats({ joints: null, skipped: ["j9: refused"] });
  const html = renderToStaticMarkup(React.createElement(NotBuiltReport, { stats: legacy, onFocusJoint: () => {} }));
  assert.match(html, /1<\/span> joint skipped/);
  assert.match(html, /j9: refused/);
});
