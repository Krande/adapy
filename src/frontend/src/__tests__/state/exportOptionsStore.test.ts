import assert from "node:assert/strict";
import test from "node:test";

// The store persists to localStorage, which node lacks: stubbed BEFORE the import.
const memory = new Map<string, string>();
(globalThis as unknown as {localStorage: unknown}).localStorage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
    clear: (): void => memory.clear(),
    key: (): string | null => null,
    get length(): number {
        return memory.size;
    },
};

const {DEFAULT_EXPORT_OPTIONS, exportOptionsChanged, exportOptionsWire, useExportOptionsStore} = await import(
    "@/state/exportOptionsStore"
);

test("the export options go to the route as export_options, AP214 and IFC4 by default", () => {
    assert.deepEqual(exportOptionsWire(DEFAULT_EXPORT_OPTIONS), {step_schema: "AP214", ifc_schema: "IFC4", ifc_swept_primitives: false});
    assert.equal(exportOptionsChanged(DEFAULT_EXPORT_OPTIONS), false);
    useExportOptionsStore.getState().setStepSchema("AP242");
    assert.deepEqual(exportOptionsWire(useExportOptionsStore.getState()), {step_schema: "AP242", ifc_schema: "IFC4", ifc_swept_primitives: false});
    assert.equal(exportOptionsChanged(useExportOptionsStore.getState()), true);
    useExportOptionsStore.getState().reset();
    useExportOptionsStore.getState().setIfcSchema("IFC4X3");
    assert.deepEqual(exportOptionsWire(useExportOptionsStore.getState()), {step_schema: "AP214", ifc_schema: "IFC4X3", ifc_swept_primitives: false});
    assert.equal(exportOptionsChanged(useExportOptionsStore.getState()), true);
    useExportOptionsStore.getState().reset();
    assert.equal(useExportOptionsStore.getState().stepSchema, "AP214");
    assert.equal(useExportOptionsStore.getState().ifcSchema, "IFC4");
});

test("the name prefixes are remembered, marked as changed, and never sent to the server", () => {
    useExportOptionsStore.getState().setPrefixProject(true);
    useExportOptionsStore.getState().setCustomPrefix("rev-b");
    const s = useExportOptionsStore.getState();
    assert.equal(exportOptionsChanged(s), true);
    // Naming the saved file only: the export (and its cache key) is the same with or without them.
    assert.deepEqual(exportOptionsWire(s), {step_schema: "AP214", ifc_schema: "IFC4", ifc_swept_primitives: false});
    useExportOptionsStore.getState().reset();
    assert.equal(useExportOptionsStore.getState().prefixProject, false);
    assert.equal(useExportOptionsStore.getState().customPrefix, "");
});

