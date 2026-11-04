import assert from "node:assert/strict";
import test from "node:test";

// `SweepDefaults` reaches `viewerApi` -> `services/auth/oidc.ts`, which reads `sessionStorage` at
// module load: stub the browser globals BEFORE the dynamic import, as jobTracking.test.ts does.
const memory = new Map<string, string>();
const storage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
    clear: (): void => memory.clear(),
    key: (): string | null => null,
    get length(): number {
        return memory.size;
    },
};
const globals = globalThis as unknown as {sessionStorage: unknown; localStorage: unknown};
globals.sessionStorage = storage;
globals.localStorage = storage;

const {excludedWithAll, toggledExcluded, withCollectionSetting, sameCollectionSettings} = await import(
    "@/components/admin/SweepDefaults"
);
const {canToggleRun, inFlightJobOf, itemSettingsLine} = await import("@/components/admin/ProviderSweepRuns");

test("a collection's own setting is set, cleared, and the collection dropped when it has none", () => {
    const start = {ALPHA: {timeout_seconds: 3600}};
    const set = withCollectionSetting(start, "BETA", "timeout_seconds", 14400);
    assert.deepEqual(set, {ALPHA: {timeout_seconds: 3600}, BETA: {timeout_seconds: 14400}});
    assert.deepEqual(start, {ALPHA: {timeout_seconds: 3600}}, "not changed in place");
    assert.deepEqual(withCollectionSetting(set, "ALPHA", "timeout_seconds", undefined), {BETA: {timeout_seconds: 14400}});
    assert.equal(sameCollectionSettings({A: {x: 1, y: 2}, B: {x: 1}}, {B: {x: 1}, A: {y: 2, x: 1}}), true);
    assert.equal(sameCollectionSettings({A: {x: 1}}, {A: {x: 2}}), false);
});

test("an item's own settings read as one line, and none as nothing", () => {
    assert.equal(itemSettingsLine({timeout_seconds: 14400}), "Own settings (sweep defaults): timeout_seconds 14400");
    assert.equal(itemSettingsLine(undefined), "");
    assert.equal(itemSettingsLine({}), "");
});

test("toggling a default keeps the excluded list sorted and unique", () => {
    assert.deepEqual(toggledExcluded(["BETA"], "ALPHA", false), ["ALPHA", "BETA"]);
    assert.deepEqual(toggledExcluded(["ALPHA", "BETA"], "BETA", true), ["ALPHA"]);
    assert.deepEqual(toggledExcluded(["BETA"], "BETA", false), ["BETA"]);
});

test("select all / deselect all switch only the given collections", () => {
    assert.deepEqual(excludedWithAll(["BETA", "GAMMA"], ["ALPHA", "BETA"], true), ["GAMMA"]);
    assert.deepEqual(excludedWithAll(["GAMMA"], ["BETA", "ALPHA"], false), ["ALPHA", "BETA", "GAMMA"]);
    assert.deepEqual(excludedWithAll([], [], false), []);
});

test("only collections a running sweep has not reached can be ticked on or off", () => {
    const item = (status: string) => ({status}) as never;
    const running = {status: "running"} as never;
    assert.equal(canToggleRun(running, item("pending")), true);
    assert.equal(canToggleRun(running, item("excluded")), true);
    for (const s of ["fetching", "publishing", "done", "error", "skipped"]) assert.equal(canToggleRun(running, item(s)), false);
    assert.equal(canToggleRun({status: "cancelled"} as never, item("pending")), false);
});

test("Extend targets the job an item has in flight: its fetch, then its publish", () => {
    const item = (status: string, fetch: string | null, publish: string | null) =>
        ({status, fetch_job_id: fetch, publish_job_id: publish}) as never;
    assert.equal(inFlightJobOf(item("fetching", "f1", null)), "f1");
    assert.equal(inFlightJobOf(item("publishing", "f1", "p1")), "p1");
    // Publishing before its job is recorded: the fetch job is still the item's.
    assert.equal(inFlightJobOf(item("publishing", "f1", null)), "f1");
    for (const s of ["pending", "done", "error", "skipped"]) assert.equal(inFlightJobOf(item(s, "f1", "p1")), null);
});
