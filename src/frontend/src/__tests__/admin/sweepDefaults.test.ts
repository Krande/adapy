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

const {excludedWithAll, toggledExcluded} = await import("@/components/admin/SweepDefaults");
const {canToggleRun} = await import("@/components/admin/ProviderSweepRuns");

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
