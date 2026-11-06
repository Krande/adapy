import React, {useMemo, useState} from "react";

import DownloadSelectionButton from "@/components/info_box_selected_object/DownloadSelectionButton";
import {ModalButton, ModalFrame} from "@/components/ui/ModalFrame";
import type {ExportScope} from "@/layout/coreDialogs";
import {runtime} from "@/runtime/config";
import {useViewerStores} from "@/state/AdaViewerContext";
import {useAssetBrowserStore} from "@/state/assetBrowserStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {
    SELECTION_EXPORT_FORMATS,
    describeExportError,
    planSelectionExport,
    runExportBody,
    selectionExportBody,
    type SelectionExportFormat,
    type SelectionExportPlan,
} from "@/utils/export/selectionExport";
import {selectionExportDeps} from "@/utils/export/selectionExportDeps";
import {buildTreeIndices, type TreeIndices} from "@/utils/tree_view/treeGraph";

// File -> Export selection… / Export model…: the exports core already has, in one dialog of
// scope and format. Both are written by the server from the model's source, as STEP or IFC (the
// object info panel's "Download as…"):
//   * Selection: the selected objects or tree level and everything under them -- the panel's own
//     control, so the group export, the geometry provider and the export options all apply;
//   * Whole model: one loaded model, from its root.
// A model with no source behind it (a GLB, a file opened from disk) cannot be exported, and the
// dialog says so.

const ROOTS = "__roots__";

/** The tree's per-model roots, under a container whatever the tree's shape (one model or many). */
export function modelRoots(tree: TreeNodeData | null): {roots: TreeNodeData[]; indices: TreeIndices | null} {
    if (!tree) return {roots: [], indices: null};
    const container: TreeNodeData = tree.id === ROOTS ? tree : {id: ROOTS, name: "", children: [tree]};
    return {roots: [...(container.children ?? [])], indices: buildTreeIndices(container)};
}

/** What the "Whole model" export of `root` would send, or why it cannot. */
export function planModelExport(root: TreeNodeData | null, indices: TreeIndices | null, restMode: boolean, loadedAssets: Parameters<typeof planSelectionExport>[0]["loadedAssets"]): SelectionExportPlan {
    if (!root) return {ok: false, reason: "No model is loaded."};
    return planSelectionExport({restMode, row: root, indices, loadedAssets});
}

const label = (n: TreeNodeData) => n.top_name || n.name;

/** The "Whole model" part: which model, which format, Export. */
const ModelExport: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const treeData = useTreeViewStore((s) => s.treeData);
    const loadedAssets = useAssetBrowserStore((s) => s.loaded);
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const {roots, indices} = useMemo(() => modelRoots(treeData), [treeData]);
    const [chosen, setChosen] = useState<string | null>(null);
    const [format, setFormat] = useState<SelectionExportFormat>(SELECTION_EXPORT_FORMATS[0].format);
    const [busy, setBusy] = useState(false);
    const [status, setStatus] = useState<{text: string; error: boolean} | null>(null);

    const root = roots.find((r) => r.id === chosen) ?? roots[0] ?? null;
    const plan = planModelExport(root, indices, runtime.isRestMode(), loadedAssets);

    const onExport = async () => {
        if (!plan.ok || busy) return;
        setBusy(true);
        setStatus({text: "Exporting…", error: false});
        const target = plan.request.target;
        const project = target.kind === "node" ? target.collection.toUpperCase() : null;
        try {
            const out = await runExportBody(selectionExportDeps(scope, project), scope, selectionExportBody(plan.request, format));
            setStatus({text: `Downloaded ${out.filename}`, error: false});
        } catch (e) {
            setStatus({text: describeExportError(e), error: true});
        } finally {
            setBusy(false);
        }
    };

    if (roots.length === 0) return <p className="text-gray-300">No model is loaded.</p>;
    return (
        <div className="space-y-3">
            <label className="block">
                <span className="block text-gray-300 mb-1">Model</span>
                <select
                    value={root?.id ?? ""}
                    onChange={(e) => setChosen(e.target.value)}
                    className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 text-xs"
                >
                    {roots.map((r) => (
                        <option key={r.id} value={r.id}>
                            {label(r)}
                        </option>
                    ))}
                </select>
            </label>
            <fieldset>
                <legend className="text-gray-300 mb-1">Format</legend>
                <div className="flex gap-4">
                    {SELECTION_EXPORT_FORMATS.map((f) => (
                        <label key={f.format} className="inline-flex items-center gap-1.5 text-xs">
                            <input type="radio" name="export-format" checked={format === f.format} onChange={() => setFormat(f.format)} />
                            {f.label}
                        </label>
                    ))}
                </div>
            </fieldset>
            {!plan.ok && <p className="text-xs text-amber-300">{plan.reason}</p>}
            <div className="flex items-center gap-2">
                <ModalButton primary onClick={() => void onExport()} disabled={!plan.ok || busy} title={plan.ok ? undefined : plan.reason}>
                    {busy ? "Exporting…" : "Export"}
                </ModalButton>
                {status && <span className={`text-xs ${status.error ? "text-red-400" : "text-gray-300"}`}>{status.text}</span>}
            </div>
        </div>
    );
};

/** The "Selection" part: the object info panel's own export control. */
const SelectionExport: React.FC = () => {
    const {useSelectedObjectStore} = useViewerStores();
    const n = useSelectedObjectStore((s) => {
        let c = 0;
        for (const ids of s.selectedObjects.values()) c += ids.size;
        return c;
    });
    if (n === 0) return <p className="text-gray-300">Nothing is selected: select objects in the view or a level in the tree.</p>;
    return (
        <div className="space-y-2">
            <p className="text-xs text-gray-300">
                {n} element{n === 1 ? "" : "s"} selected. The export holds the selection and everything under it; the
                options button sets the schemas and the file name.
            </p>
            <DownloadSelectionButton />
        </div>
    );
};

export const ExportScopeTabs: React.FC<{scope: ExportScope; onScope: (s: ExportScope) => void}> = ({scope, onScope}) => (
    <div role="radiogroup" aria-label="What to export" className="flex gap-4 mb-3">
        {(
            [
                ["selection", "Selection"],
                ["model", "Whole model"],
            ] as const
        ).map(([id, text]) => (
            <label key={id} className="inline-flex items-center gap-1.5">
                <input type="radio" name="export-scope" checked={scope === id} onChange={() => onScope(id)} />
                {text}
            </label>
        ))}
    </div>
);

const ExportDialog: React.FC<{scope: ExportScope; onClose: () => void}> = ({scope: initial, onClose}) => {
    const [scope, setScope] = useState<ExportScope>(initial);
    return (
        <ModalFrame title="Export" onClose={onClose} testId="export-dialog">
            <ExportScopeTabs scope={scope} onScope={setScope} />
            <p className="text-xs text-gray-400 mb-3">
                Written by the server from the model&apos;s source file, as STEP or IFC, and downloaded.
            </p>
            {scope === "selection" ? <SelectionExport /> : <ModelExport />}
        </ModalFrame>
    );
};

export default ExportDialog;
