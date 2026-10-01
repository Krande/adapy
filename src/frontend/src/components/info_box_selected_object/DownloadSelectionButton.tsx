import React, {useMemo, useState} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {runtime} from '@/runtime/config';
import {scopeUrlPart, useScopeStore} from '@/state/scopeStore';
import {useAssetBrowserStore} from '@/state/assetBrowserStore';
import {buildTreeIndices} from '@/utils/tree_view/treeGraph';
import {selectionExportApi} from '@/services/api/selectionExport';
import {conversionApi} from '@/services/api/conversion';
import {filesApi} from '@/services/api/files';
import {trackJob} from '@/services/jobTracking';
import {
    SELECTION_EXPORT_FORMATS,
    describeExportError,
    planSelectionExport,
    resolveSelectedRow,
    runSelectionExport,
    type SelectionExportFormat,
} from '@/utils/export/selectionExport';

/**
 * "Download as…" for the selected object or tree level, with everything under it, as STEP or IFC.
 *
 * Whether it applies is decided by `planSelectionExport` from what the client already knows --
 * where the selected row's model came from -- so a selection that cannot be exported shows a
 * disabled button and the reason, rather than a job that fails two polls later. Without a
 * server there is nothing to export with at all, and the button is not shown.
 */
const DownloadSelectionButton: React.FC = () => {
    const {useObjectInfoStore, useSelectedObjectStore, useTreeViewStore} = useViewerStores();
    const name = useObjectInfoStore((s) => s.name);
    const selectedNodeId = useObjectInfoStore((s) => s.selectedNodeId);
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const treeData = useTreeViewStore((s) => s.treeData);
    const findByRange = useTreeViewStore((s) => s.findNodeByRangeId);
    const loadedAssets = useAssetBrowserStore((s) => s.loaded);
    const scope = scopeUrlPart(useScopeStore((s) => s.current));

    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState<SelectionExportFormat | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Keyed on treeData identity, as TreeNodeInfoSection's are: the loader replaces the tree on
    // every model load, so this rebuilds exactly when the tree changes.
    const indices = useMemo(() => (treeData ? buildTreeIndices(treeData) : null), [treeData]);

    const plan = useMemo(() => {
        const ranges: [string, string][] = [];
        selectedObjects.forEach((ids, mesh) => {
            // The same lookup key the 3D click's tree sync resolves rows by (handleClickMesh).
            const m = mesh as unknown as {unique_key?: string; userData?: Record<string, unknown>};
            const key = m.unique_key ?? (m.userData?.['unique_hash'] as string | undefined);
            if (key) ids.forEach((rangeId) => ranges.push([key, String(rangeId)]));
        });
        const row = resolveSelectedRow({indices, selectedNodeId, name, ranges, findByRange});
        return planSelectionExport({restMode: runtime.isRestMode(), row, indices, loadedAssets});
    }, [indices, selectedNodeId, name, selectedObjects, findByRange, loadedAssets]);

    // Nothing here can work without a server, so there is nothing to offer -- not even a disabled
    // button, which would only advertise a feature this viewer cannot have.
    if (!runtime.isRestMode()) return null;

    const onExport = async (format: SelectionExportFormat) => {
        if (!plan.ok || busy) return;
        setOpen(false);
        setBusy(format);
        setError(null);
        try {
            await runSelectionExport(
                {
                    api: {
                        exportSelection: (s, body) => selectionExportApi.exportSelection(s, body),
                        async jobStatus(jobId) {
                            const status = await conversionApi.convertStatus(jobId);
                            return {status: status.status, error: status.error};
                        },
                    },
                    download: (s, key, filename) => filesApi.downloadBlob(s, key, filename),
                    trackJob: (opts) => void trackJob({...opts, scopeUrl: scope}),
                },
                scope,
                plan.request,
                format,
            );
        } catch (e) {
            setError(describeExportError(e));
        } finally {
            setBusy(null);
        }
    };

    const title = plan.ok
        ? (plan.request.element === null
            ? `Download the whole model "${plan.request.label}" as STEP or IFC`
            : `Download "${plan.request.element}" and everything under it as STEP or IFC`)
        : plan.reason;

    return (
        <div className="relative inline-flex flex-col">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                disabled={!plan.ok || busy !== null}
                className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 disabled:hover:bg-gray-700 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                title={title}
                aria-label="Download selection as"
                aria-haspopup="menu"
                aria-expanded={open}
            >
                <DownloadIcon/>
                {busy ? `Exporting ${busy.toUpperCase()}…` : 'Download as…'}
            </button>
            {open && plan.ok && (
                <div role="menu" className="absolute top-full left-0 mt-1 z-10 bg-gray-800 border border-gray-600 rounded-sm shadow-lg min-w-32">
                    {SELECTION_EXPORT_FORMATS.map(({format, label}) => (
                        <button
                            key={format}
                            type="button"
                            role="menuitem"
                            onClick={() => void onExport(format)}
                            className="block w-full text-left text-[11px] text-white px-2 py-1 hover:bg-gray-600"
                        >
                            {label}
                        </button>
                    ))}
                </div>
            )}
            {/* The reason is on screen, not only in the tooltip: a tooltip does not exist on a
                touch screen, and "why is this greyed out" is the first question it raises. */}
            {!plan.ok && <div className="text-[10px] opacity-70 mt-0.5 max-w-64">{plan.reason}</div>}
            {error && <div className="text-[10px] text-red-400 mt-0.5 max-w-64 break-words">{error}</div>}
        </div>
    );
};

const DownloadIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <path d="M8 2v8M4.5 6.5 8 10l3.5-3.5M2.5 13.5h11"/>
    </svg>
);

export default DownloadSelectionButton;
