// Admin -> Robot bundles: the selection rules, the preview's statuses and choices, and the tab as it
// draws the list, the import preview and the results -- including what keeps it usable on a phone
// (touch-sized controls, wrapping names, a real file input with no `accept` filter).
import assert from "node:assert/strict";
import { test } from "node:test";

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const rb = await import("@/assets/robotBundles");
const { RobotBundlesView } = await import("@/components/admin/RobotBundlesTab");
const { parseTabId } = await import("@/components/admin/adminTabs");
const { PLUGIN_API_VERSION } = await import("@/plugins/registry");
const { VIEWER_CORE_API_VERSION } = await import("@/viewer-core");
const { readFileSync } = await import("node:fs");
const appSource = readFileSync(new URL("../../viewer-core/app.ts", import.meta.url), "utf8");

type Row = import("@/assets/robotBundles").RobotAssetRow;
type Plan = import("@/assets/robotBundles").RobotImportPlanRow;

const LONG = "Synthetic six-axis test arm with an exceptionally long descriptive name that must wrap";

const version = (id: string, v: string, latest = false) => ({
  ref: `${id}@${v}`,
  version: v,
  version_scheme: "revision" as const,
  latest,
  files: 3,
  bytes: 4096,
  manifest_sha256: "a".repeat(64),
  last_modified: null,
  metadata: {},
  problem: null,
});

const rows: Row[] = [
  { id: "arm-a", name: LONG, latest: "2", versions: [version("arm-a", "2", true), version("arm-a", "1")] },
  { id: "arm-b", name: "Arm B", latest: "1", versions: [version("arm-b", "1", true)] },
];

const list = {
  store: "providers",
  provider: "robot-assets",
  collection: "robots",
  robots: rows,
  grants: [{ target_scope: "shared", collection: "robots", geometry: true }],
  bundle: {
    schema: "ada.robots/bundle@1",
    suffix: ".adabots",
    limits: { max_bundle_bytes: 1000, max_total_bytes: 1, max_file_bytes: 1, max_files: 1, max_robots: 1 },
  },
};

const plan = (ref: string, status: Plan["status"], extra: Partial<Plan> = {}): Plan => {
  const [id, v] = ref.split("@");
  return {
    ref,
    id,
    version: v,
    version_scheme: "revision",
    name: id,
    status,
    detail: "",
    latest_before: "3",
    stored_versions: [],
    files: 2,
    bytes: 100,
    content_sha256: "b".repeat(64),
    metadata: {},
    problems: [],
    duplicate_of: null,
    ...extra,
  };
};

const preview = {
  upload_id: "f".repeat(32),
  bundle: { schema: "ada.robots/bundle@1", exported_at: "2026-01-01T00:00:00Z", adapy_version: "0.168.0", robots: 5 },
  robots: [
    plan("arm-new@1", "new"),
    plan("arm-u@1", "unchanged"),
    plan("arm-n@4", "newer"),
    plan("arm-o@2", "older"),
    plan("arm-c@1", "conflict", { detail: "arm-c@1 is stored with other content" }),
  ],
  counts: { new: 1, unchanged: 1, newer: 1, older: 1, conflict: 1, invalid: 0 },
  grants: [],
};

const noop = () => {};
const view = (over: Record<string, unknown> = {}) =>
  renderToStaticMarkup(
    React.createElement(RobotBundlesView, {
      list,
      loading: false,
      error: null,
      selected: new Set<string>(),
      exporting: false,
      importState: { phase: "idle" },
      onRefresh: noop,
      onSelect: noop,
      onExport: noop,
      onStartImport: noop,
      onFile: noop,
      onChoices: noop,
      onImport: noop,
      onCancelImport: noop,
      ...over,
    } as never),
  );

// --- the rules ------------------------------------------------------------------------------------------

test("a robot's checkbox selects all of its versions, and says when only some are", () => {
  let sel = rb.toggleRobot(new Set(), rows[0]);
  assert.deepEqual([...sel].sort(), ["arm-a@1", "arm-a@2"]);
  assert.equal(rb.robotSelection(sel, rows[0]), "all");
  sel = rb.toggleRef(sel, "arm-a@1");
  assert.equal(rb.robotSelection(sel, rows[0]), "some");
  sel = rb.toggleRobot(sel, rows[0]);
  assert.equal(rb.robotSelection(sel, rows[0]), "all", "a partly selected robot becomes all selected");
  sel = rb.toggleRobot(sel, rows[0]);
  assert.equal(sel.size, 0);
  assert.deepEqual(rb.allRefs(rows), ["arm-a@2", "arm-a@1", "arm-b@1"]);
  assert.deepEqual([...rb.pruneSelection(new Set(["arm-a@1", "gone@1"]), rows)], ["arm-a@1"]);
  assert.deepEqual(rb.exportRefs(new Set(["arm-b@1", "arm-a@2"])), ["arm-a@2", "arm-b@1"]);
});

test("the preview's statuses, and what an import with the choices writes", () => {
  assert.equal(rb.statusLabel({ status: "newer", version: "4" }), "Newer: adds version 4");
  assert.equal(rb.statusLabel({ status: "older", version: "2" }), "Older: skipped");
  assert.equal(rb.statusTone("conflict"), "warn");
  assert.equal(rb.statusTone("failed"), "bad");
  const rows = preview.robots;
  assert.equal(rb.writeCount(rows, {}), 2, "new and newer");
  assert.equal(rb.writeCount(rows, { add_older: true }), 3);
  let c = rb.setRobotChoice({}, "arm-new@1", "include", false);
  assert.equal(rb.writeCount(rows, c), 1);
  c = rb.setRobotChoice(c, "arm-o@2", "add_older", true);
  assert.equal(rb.writeCount(rows, c), 2);
  const all = rb.setAddOlderForAll(c, false);
  assert.equal(all.robots?.["arm-o@2"], undefined, "the bundle-wide switch replaces a robot's own");
  assert.deepEqual(all.robots?.["arm-new@1"], { include: false }, "include choices stay");
  assert.equal(rb.planLabel(rows[3], {}), "Older: skipped");
  assert.equal(rb.planLabel(rows[3], { add_older: true }), "Older: adds a past version");
  assert.equal(rb.planLabel(rows[0], rb.setRobotChoice({}, "arm-new@1", "include", false)), "New (left out)");
  assert.equal(rb.willWrite(rows[1], { add_older: true }), false, "unchanged is never written");
  assert.equal(rb.willWrite(rows[4], { add_older: true }), false, "a conflict is never written");
});

test("a picked file is checked by its name and size before it is sent", () => {
  assert.equal(rb.bundleFileProblem({ name: "robots.adabots", size: 10 }), null);
  assert.equal(rb.bundleFileProblem({ name: "ROBOTS.ZIP", size: 10 }), null);
  assert.match(rb.bundleFileProblem({ name: "photo.jpg", size: 10 })!, /not a robot bundle/);
  assert.match(rb.bundleFileProblem({ name: "a.adabots", size: 0 })!, /empty/);
  assert.match(rb.bundleFileProblem({ name: "a.adabots", size: 2000 }, list.bundle.limits)!, /the limit is/);
});

test("a refused bundle's problems are read from the error body", () => {
  const body = JSON.stringify({ detail: { message: "the bundle is refused", problems: ["'../x': no", "two"] } });
  assert.deepEqual(rb.refusalProblems(body), ["'../x': no", "two"]);
  assert.deepEqual(rb.refusalProblems(JSON.stringify({ detail: "plain" })), ["plain"]);
  assert.deepEqual(rb.refusalProblems("<html>"), []);
  assert.equal(rb.filenameFromDisposition('attachment; filename="arm-a@1.adabots"', "x"), "arm-a@1.adabots");
  assert.equal(rb.filenameFromDisposition(null, "x.adabots"), "x.adabots");
  assert.equal(
    rb.resultSummary({ results: [], counts: { added: 2, "added-older": 1, conflict: 1 }, index_written: true }),
    "3 added · 1 in conflict",
  );
});

// --- the tab -------------------------------------------------------------------------------------------

test("the list: every robot with its versions, multi-select and the download buttons", () => {
  const html = view({ selected: new Set(["arm-a@2"]) });
  assert.match(html, /data-robot-id="arm-a"/);
  assert.match(html, /data-ref="arm-a@1"/);
  assert.match(html, /data-testid="select-robot-arm-a"/);
  assert.match(html, /aria-checked="mixed"[^>]*data-testid="select-robot-arm-a"/, "one of two versions selected");
  assert.match(html, /data-testid="download-selected"[^>]*>Download selected \(1\)/);
  assert.match(html, /data-testid="download-all"/);
  assert.match(html, /data-testid="import-bundle"/);
  assert.match(html, /Read by shared\./);
  assert.match(html, />latest</);
});

test("nothing selected: Download selected is off", () => {
  const html = view();
  assert.match(html, /disabled=""[^>]*data-testid="download-selected"/);
});

test("phone layout: touch-sized targets, 16 px gutters, names that wrap, no sideways scroll", () => {
  const html = view({ importState: { phase: "pick" } });
  assert.match(html, /min-h-11/, "44 px controls on touch");
  assert.match(html, /class="px-4 py-3/, "16 px side gutters");
  assert.match(html, /overflow-x-hidden/);
  assert.match(html, /break-words">Synthetic six-axis/, "a long name wraps");
  assert.match(html, /grid grid-cols-2 gap-2 sm:flex/, "the buttons stack in two columns on a phone");
  const input = /<input type="file"[^>]*>/.exec(html)![0];
  assert.doesNotMatch(input, /accept=/, "no accept filter: iOS would grey out a .adabots file");
  assert.match(html, /<label[^>]*data-testid="bundle-dropzone"/, "the drop zone is the input's label (a tap opens the picker)");
});

test("uploading shows its progress", () => {
  const html = view({ importState: { phase: "uploading", fileName: "robots.adabots", sent: 0.42 } });
  assert.match(html, /Uploading robots\.adabots… 42%/);
  assert.match(html, /role="progressbar"[^>]*aria-valuenow="42"/);
});

test("the preview: one line per robot with its status, the older one's choice, the import button", () => {
  const html = view({
    importState: { phase: "preview", fileName: "robots.adabots", preview, choices: {} },
  });
  assert.match(html, /data-phase="preview"/);
  for (const [ref, status] of [
    ["arm-new@1", "new"],
    ["arm-u@1", "unchanged"],
    ["arm-n@4", "newer"],
    ["arm-o@2", "older"],
    ["arm-c@1", "conflict"],
  ]) {
    assert.match(html, new RegExp(`data-plan-ref="${ref}" data-status="${status}"`));
  }
  assert.match(html, /Newer: adds version 4/);
  assert.match(html, /Older: skipped/);
  assert.match(html, /the latest stays 3/);
  assert.match(html, /arm-c@1 is stored with other content/);
  assert.match(html, /data-testid="plan-include-arm-o@2"/, "an older version can be added");
  assert.doesNotMatch(html, /data-testid="plan-include-arm-c@1"/, "a conflict cannot be chosen");
  assert.doesNotMatch(html, /data-testid="plan-include-arm-u@1"/);
  assert.match(html, /data-testid="plan-add-older"/);
  assert.match(html, /data-testid="plan-import"[^>]*>Import 2 robots/);
  assert.match(html, /no scope holds the robots grant yet/);
});

test("a refused bundle says why, line by line", () => {
  const html = view({
    importState: { phase: "refused", fileName: "evil.adabots", message: "the bundle is refused", problems: ["'../x.json': no"] },
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /evil\.adabots was refused/);
  assert.match(html, /&#x27;\.\.\/x\.json&#x27;: no/);
});

test("the results", () => {
  const html = view({
    importState: {
      phase: "done",
      fileName: "robots.adabots",
      result: {
        results: [
          { ref: "arm-new@1", id: "arm-new", version: "1", name: "x", planned: "new", result: "added", detail: "adds" },
          { ref: "arm-c@1", id: "arm-c", version: "1", name: "x", planned: "conflict", result: "conflict", detail: "no" },
        ],
        counts: { added: 1, conflict: 1 },
        index_written: true,
      },
    },
  });
  assert.match(html, /data-testid="import-results"/);
  assert.match(html, /robots\.adabots: 1 added · 1 in conflict/);
  assert.match(html, /data-result-ref="arm-c@1"/);
});

test("the tab has a deep link, and the API is on the plugin surface from 1.37.0", () => {
  assert.deepEqual(parseTabId("robots", new Set()), { tab: "robots" });
  const atLeast = (v: string, min: string) => {
    const [a, b] = [v, min].map((s) => s.split(".").map(Number));
    for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
    return true;
  };
  assert.ok(atLeast(PLUGIN_API_VERSION, "1.37.0"));
  assert.equal(VIEWER_CORE_API_VERSION, PLUGIN_API_VERSION);
  assert.match(appSource, /export \{ robotBundlesApi, saveBundle \} from "@\/services\/api\/robotBundles"/);
  assert.match(appSource, /export \{ ROBOT_BUNDLE_SCHEMA, ROBOT_BUNDLE_SUFFIX \}/);
});
