// The Storage panel's options: absent, the panel renders as it did; present, each does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { StorageBody } = await import("@/components/storage/StorageBrowser");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { useServerInfoStore } = await import("@/state/serverInfoStore");
const { useScopeStore } = await import("@/state/scopeStore");
const { useLoadQueueStore } = await import("@/state/loadQueueStore");
const { useConversionStore } = await import("@/state/conversionStore");
const { unsavedModelName } = await import("@/components/storage/browser/UnsavedModelRow");
const { buildFileMenuItems, buildProceduralMenuItems, selectionMenuItems } = await import(
    "@/components/storage/storageMenuItems"
);
const { claimLoad, isLoadClaimed, releaseLoad, loadClaimsVersion, subscribeLoadClaims } = await import(
    "@/plugins/loadClaims"
);
const { IDLE_ROW_LOAD, jobPercent, resolveRowLoadState, rowStageLabel } = await import(
    "@/components/storage/browser/rowLoadProgress"
);

type Props = import("@/components/storage/StorageBrowser").StorageBodyProps;
type ConversionJob = import("@/state/conversionStore").ConversionJob;
type ServerFileEntry = import("@/state/serverInfoStore").ServerFileEntry;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(StorageBody, props)));
}

const FILE = { name: "part.glb", fileType: 0, filepath: "part.glb", lastModified: "2026-09-17T10:49:00Z" } as ServerFileEntry;

function withFile<T>(fn: () => T): T {
    setServerState(useServerInfoStore, { serverFileObjects: [FILE] });
    setServerState(useServerInfoStore, { showServerInfoBox: true } as never);
    try {
        return fn();
    } finally {
        setServerState(useServerInfoStore, { serverFileObjects: [] });
    }
}

const job = (over: Partial<ConversionJob> = {}): ConversionJob => ({
    sourceKey: "",
    jobId: "j-1",
    derivedKey: "",
    status: "running",
    progress: 0.42,
    stage: "tessellating",
    error: null,
    startedAt: 1,
    ...over,
});

test("without options the panel renders as with an empty options object, and as with the defaults", () => {
    withFile(() => {
        const plain = render();
        assert.equal(render({}), plain);
        assert.equal(
            render({ rowLoadProgress: false, selectionActions: "toolbar", scopePicker: false, showModified: true }),
            plain,
        );
        assert.equal(render({ maximized: true, setMaximized: () => {} }), plain, "the deprecated props are ignored");
        assert.match(plain, /scope: /);
        assert.match(plain, /tabular-nums/, "the Modified column");
    });
});

test("the header has no Maximize; onClose adds a Close button", () => {
    const plain = render();
    assert.doesNotMatch(plain, /Maximize/);
    assert.doesNotMatch(plain, /aria-label="Close storage"/);
    assert.match(render({ onClose: () => {} }), /aria-label="Close storage"/);
});

test("showModified false hides the Modified column; auto shows it until it has measured the panel", () => {
    withFile(() => {
        assert.doesNotMatch(render({ showModified: false }), /tabular-nums/);
        assert.equal(render({ showModified: "auto" }), render());
    });
});

test("scopePicker turns the scope line into a picker when there is a choice", () => {
    const scopes = [
        { kind: "personal", id: "u1", name: "Mine" },
        { kind: "project", id: "p1", name: "Project one" },
    ];
    assert.doesNotMatch(render({ scopePicker: true }), /<select[^>]*aria-label="Scope"/, "no choice, no picker");
    setServerState(useScopeStore, { available: scopes, current: scopes[0] } as never);
    try {
        const html = render({ scopePicker: true });
        assert.match(html, /<select[^>]*aria-label="Scope"/);
        assert.match(html, /Project one \(project\)/);
        assert.doesNotMatch(render(), /aria-label="Scope"/);
    } finally {
        setServerState(useScopeStore, { available: [], current: null } as never);
    }
});

test("rowLoadProgress tells the current load's percent and stage on its row", () => {
    withFile(() => {
        setServerState(useLoadQueueStore, { current: { name: FILE.name } });
        setServerState(useConversionStore, { jobs: { [`${FILE.name}::glb`]: job() } });
        try {
            const html = render({ rowLoadProgress: true });
            assert.match(html, /role="progressbar"[^>]*aria-valuenow="42"/);
            assert.match(html, /Tessellating geometry…/);
            assert.match(html, />42%</);
            assert.match(html, /aria-label="Cancel loading part.glb"/);
            assert.doesNotMatch(render(), /role="progressbar"/, "off: the plain bar");
        } finally {
            setServerState(useLoadQueueStore, { current: null });
            setServerState(useConversionStore, { jobs: {} });
        }
    });
});

test("rowLoadProgress shows a failed load on its row with a Dismiss", () => {
    withFile(() => {
        setServerState(useLoadQueueStore, { errors: [{ name: FILE.name, message: "no converter" }] });
        try {
            const html = render({ rowLoadProgress: true });
            assert.match(html, /Load failed: no converter/);
            assert.match(html, /aria-label="Dismiss load status for part.glb"/);
            assert.doesNotMatch(render(), /Load failed/);
        } finally {
            setServerState(useLoadQueueStore, { errors: [] });
        }
    });
});

test("an open model missing from the saved list gets an unsaved row", () => {
    assert.doesNotMatch(render(), /unsaved/);
    setServerState(useCellBuilderStore, { active: { modelId: "m-1", name: "Draft model", revision: 0 } });
    try {
        const html = render();
        assert.match(html, /aria-label="Close Draft model"/);
        assert.match(html, />unsaved</);
    } finally {
        setServerState(useCellBuilderStore, { active: null });
    }
});

test("unsavedModelName", () => {
    const active = { modelId: "m-1", name: "A" };
    assert.equal(unsavedModelName(null, []), null);
    assert.equal(unsavedModelName(active, []), "A");
    assert.equal(unsavedModelName(active, [{ id: "m-2" }]), "A");
    assert.equal(unsavedModelName(active, [{ id: "m-1" }]), null);
});

const fileCtx = { isLoaded: false, busy: false, canMutate: true, onToggle: () => {} };
const keys = (items: { key: string }[]) => items.map((i) => i.key);

test("the file menu offers Convert only when set, Cancel in the toggle's place while loading, and Dismiss", () => {
    const plain = keys(buildFileMenuItems(FILE, fileCtx));
    assert.ok(!plain.includes("convert"));
    const withConvert = keys(buildFileMenuItems(FILE, { ...fileCtx, onConvert: () => {} }));
    assert.equal(withConvert[1], "convert");
    const loading = keys(buildFileMenuItems(FILE, { ...fileCtx, busy: true, onCancelLoad: () => {} }));
    assert.equal(loading[0], "cancel-load");
    assert.ok(!loading.includes("toggle-load"));
    assert.equal(keys(buildFileMenuItems(FILE, { ...fileCtx, onCancelLoad: () => {} }))[0], "toggle-load", "only while busy");
    assert.ok(keys(buildFileMenuItems(FILE, { ...fileCtx, onDismissLoadStatus: () => {} })).includes("dismiss-load-status"));
});

test("a selection of two or more puts its actions at the end of a selected row's menu", () => {
    const sel = { count: 3, hasVersions: false, onLoad() {}, onUnload() {}, onMove() {}, onDelete() {}, onClear() {} };
    assert.deepEqual(selectionMenuItems(undefined), []);
    assert.deepEqual(selectionMenuItems({ ...sel, count: 1 }), []);
    const items = selectionMenuItems(sel);
    assert.deepEqual(keys(items), ["selection-load", "selection-unload", "selection-move", "selection-clear", "selection-delete"]);
    assert.equal(items[0].label, "Load 3 selected");
    assert.equal(items[0].separatorBefore, true);
    const versions = selectionMenuItems({ ...sel, hasVersions: true });
    assert.equal(versions.find((i) => i.key === "selection-delete")?.disabled, true);
    assert.deepEqual(keys(selectionMenuItems({ ...sel, onMove: undefined, onDelete: undefined })), [
        "selection-load",
        "selection-unload",
        "selection-clear",
    ]);
    const file = keys(buildFileMenuItems(FILE, { ...fileCtx, selection: sel }));
    assert.deepEqual(file.slice(-5), keys(items));
    const model = keys(buildProceduralMenuItems("m", { canMutate: true, onOpen: () => {}, selection: sel }));
    assert.deepEqual(model.slice(-5), keys(items));
});

test("load claims are counted, and only a source's own load jobs are claimed with it", () => {
    let calls = 0;
    const off = subscribeLoadClaims(() => calls++);
    const v0 = loadClaimsVersion();
    const a = claimLoad("x.ifc");
    const b = claimLoad("x.ifc");
    assert.equal(isLoadClaimed("x.ifc"), true);
    assert.equal(isLoadClaimed("x.ifc", "x.ifc::glb"), true);
    assert.equal(isLoadClaimed("x.ifc", "x.ifc::fea"), true);
    assert.equal(isLoadClaimed("x.ifc", "x.ifc::step"), false, "a conversion to another format stays the toast's");
    assert.equal(isLoadClaimed("y.ifc"), false);
    a();
    a();
    assert.equal(isLoadClaimed("x.ifc"), true, "a release counts once");
    b();
    assert.equal(isLoadClaimed("x.ifc"), false);
    releaseLoad("x.ifc");
    off();
    assert.equal(calls, 4, "a release with nothing claimed changes nothing");
    assert.ok(loadClaimsVersion() > v0);
});

test("resolveRowLoadState", () => {
    const base = { jobs: {}, loadCurrentName: null, queuedNames: new Set<string>(), loadErrors: [], cancelledNames: new Set<string>() };
    assert.deepEqual(resolveRowLoadState("a", base), IDLE_ROW_LOAD);
    assert.equal(resolveRowLoadState("a", { ...base, queuedNames: new Set(["a"]) }).phase, "queued");
    const loading = resolveRowLoadState("a", { ...base, loadCurrentName: "a", jobs: { "a::glb": job() } });
    assert.deepEqual([loading.phase, loading.percent, loading.cancel], ["loading", 42, { storeKey: "a::glb", jobId: "j-1" }]);
    const download = resolveRowLoadState("a", {
        ...base,
        loadCurrentName: "a",
        jobs: { "model-load": job({ jobId: "", stage: "Downloading x.glb (2 MB)", derivedKey: "x.glb" }) },
    });
    assert.equal(download.cancel, null, "the download cannot be stopped");
    assert.equal(download.stage, "Downloading (2 MB)");
    assert.equal(
        resolveRowLoadState("a", { ...base, loadCurrentName: "a", jobs: { "a::fea": job({ jobId: "" }) } }).cancel?.storeKey,
        "a::fea",
        "a streaming bake stops before it has an id",
    );
    const failed = resolveRowLoadState("a", { ...base, jobs: { "a::glb": job({ status: "error", error: "bad" }) } });
    assert.deepEqual([failed.phase, failed.message, failed.dismissKeys], ["error", "bad", ["a::glb"]]);
    const cancelled = resolveRowLoadState("a", {
        ...base,
        cancelledNames: new Set(["a"]),
        jobs: { "a::glb": job({ status: "error" }) },
    });
    assert.equal(cancelled.phase, "cancelled", "a cancel outranks the error it causes");
    assert.equal(resolveRowLoadState("a", { ...base, loadErrors: [{ name: "a", message: "m" }] }).message, "m");
    assert.equal(jobPercent(job({ status: "queued" })), null);
    assert.equal(jobPercent(job({ progress: 2 })), 100);
    assert.equal(rowStageLabel("a::glb", job({ stage: "writing" })), "Writing output…");
});

test("the progress toast leaves a claimed load out by default", async () => {
    const { default: ConversionProgress } = await import("@/components/conversion/ConversionProgress");
    const toast = (props = {}) => renderToStaticMarkup(React.createElement(ConversionProgress, props));
    setServerState(useLoadQueueStore, { current: { name: "claimed.glb" } });
    try {
        assert.match(toast(), /claimed\.glb/);
        const release = claimLoad("claimed.glb");
        try {
            assert.doesNotMatch(toast(), /claimed\.glb/);
            assert.doesNotMatch(toast({ isClaimed: () => false }), /claimed\.glb/, "a host's predicate adds to the claims");
        } finally {
            release();
        }
        assert.match(toast(), /claimed\.glb/);
        assert.doesNotMatch(toast({ isClaimed: (n: string) => n === "claimed.glb" }), /claimed\.glb/, "or claims on its own");
    } finally {
        setServerState(useLoadQueueStore, { current: null });
    }
});