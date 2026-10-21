import React, {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {useViewerStores} from '@/state/AdaViewerContext';
import {runtime} from '@/runtime/config';
import {scopeUrlPart, useScopeStore} from '@/state/scopeStore';
import {useAssetBrowserStore} from '@/state/assetBrowserStore';
import {useProviderName} from '@/state/providerNamesStore';
import {buildTreeIndices} from '@/utils/tree_view/treeGraph';
import {captureSelection} from '@/utils/groups/groupScene';
import {selectionExportApi} from '@/services/api/selectionExport';
import {clashCheckApi, type WireGeometryProvider} from '@/services/api/clashCheck';
import {conversionApi} from '@/services/api/conversion';
import {filesApi} from '@/services/api/files';
import {trackJob} from '@/services/jobTracking';
import {
    SELECTION_EXPORT_FORMATS,
    describeExportError,
    groupExportBlocker,
    groupExportBody,
    planGroupExport,
    planSelectionExport,
    resolveSelectedRow,
    runExportBody,
    selectionExportBody,
    type GroupExportLog,
    type SelectionExportDeps,
    type SelectionExportFormat,
} from '@/utils/export/selectionExport';

/**
 * "Download as…" for the selection, with everything under it, as STEP or IFC.
 *
 * One selected object or tree level goes through `planSelectionExport`. Several, or one read from
 * another provider than its own, are sent as a group and written as ONE file, each element under
 * the levels it sits in at its source. "Geometry from" chooses where node members are read from;
 * when part of the selection was delivered as a mesh it starts on a provider that publishes
 * objects, since a mesh has none to write.
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
    const providerName = useProviderName();

    const [open, setOpen] = useState(false);
    const buttonRef = useRef<HTMLButtonElement>(null);
    const closeMenu = useCallback(() => setOpen(false), []);
    const [busy, setBusy] = useState<SelectionExportFormat | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [note, setNote] = useState<{text: string; title: string} | null>(null);
    const [providers, setProviders] = useState<readonly WireGeometryProvider[] | null>(null);
    // `undefined`: the plan's default; a value (null included) is the user's choice.
    const [chosen, setChosen] = useState<string | null | undefined>(undefined);

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

    // eslint-disable-next-line react-hooks/exhaustive-deps
    const captured = useMemo(() => captureSelection(), [selectedObjects, treeData, loadedAssets]);
    const group = useMemo(
        () => planGroupExport({members: captured.members, loadedAssets, providers, providerName}),
        [captured, loadedAssets, providers, providerName],
    );

    useEffect(() => setChosen(undefined), [captured]);
    useEffect(() => {
        if (!runtime.isRestMode()) return;
        let live = true;
        clashCheckApi
            .listGeometryProviders(scope)
            .then((list) => live && setProviders(list))
            .catch(() => live && setProviders(null));
        return () => {
            live = false;
        };
    }, [scope]);

    if (!runtime.isRestMode()) return null;

    const provider = chosen === undefined ? group.defaultProvider : chosen;
    const count = captured.members.length;
    const useGroup = count > 0 && (count > 1 || provider !== null || !plan.ok);
    const blocker = useGroup ? groupExportBlocker(group, provider) : plan.ok ? null : plan.reason;
    const reason = blocker ?? (count === 0 && !plan.ok ? captured.reason ?? plan.reason : null);
    const enabled = reason === null;

    const groupLabel =
        count === 1
            ? captured.members[0].element ?? (plan.ok ? plan.request.label : 'selection')
            : `selection of ${count}`;

    const deps: SelectionExportDeps = {
        api: {
            exportSelection: (s, body) => selectionExportApi.exportSelection(s, body),
            async jobStatus(jobId) {
                const status = await conversionApi.convertStatus(jobId);
                return {status: status.status, error: status.error};
            },
        },
        download: (s, key, filename) => filesApi.downloadBlob(s, key, filename),
        trackJob: (opts) => void trackJob({...opts, scopeUrl: scope}),
    };

    const onExport = async (format: SelectionExportFormat) => {
        if (!enabled || busy) return;
        setOpen(false);
        setBusy(format);
        setError(null);
        setNote(null);
        try {
            if (!useGroup && plan.ok) {
                await runExportBody(deps, scope, selectionExportBody(plan.request, format));
                return;
            }
            const out = await runExportBody(deps, scope, groupExportBody(captured.members, groupLabel, format, provider));
            const log = await readLog(scope, out.derivedKey);
            if (log?.warnings.length) {
                setNote({
                    text: `${log.warnings.length} of ${log.members} left out`,
                    title: log.warnings.join('\n'),
                });
            }
        } catch (e) {
            setError(describeExportError(e));
        } finally {
            setBusy(null);
        }
    };

    const title = !enabled
        ? reason ?? ''
        : useGroup
            ? `Download ${count === 1 ? `"${groupLabel}"` : `${count} elements`} as one STEP or IFC, each under the levels it sits in`
            : plan.ok && plan.request.element === null
                ? `Download the whole model "${plan.request.label}" as STEP or IFC`
                : `Download "${plan.ok ? plan.request.element : ''}" and everything under it as STEP or IFC`;
    const showSource = group.hasNodes && (group.choices.length > 1 || group.meshProviders.length > 0);

    return (
        <div className="relative inline-flex flex-col">
            <button
                ref={buttonRef}
                type="button"
                onClick={() => setOpen((o) => !o)}
                disabled={busy !== null || (!enabled && !showSource)}
                className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 disabled:hover:bg-gray-700 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                title={title}
                aria-label="Download selection as"
                aria-haspopup="menu"
                aria-expanded={open}
            >
                <DownloadIcon/>
                {busy ? `Exporting ${busy.toUpperCase()}…` : count > 1 ? `Download as… (${count})` : 'Download as…'}
            </button>
            {open && (
                <FloatingMenu anchorRef={buttonRef} onClose={closeMenu}>
                    {showSource && (
                        <label className="block px-2 py-1 text-[10px] text-gray-300 border-b border-gray-700">
                            Geometry from
                            <select
                                className="mt-0.5 block w-full rounded-sm bg-gray-700 text-[11px] text-white"
                                value={provider ?? ''}
                                onChange={(e) => setChosen(e.target.value || null)}
                            >
                                {group.choices.map((c) => (
                                    <option key={c.id ?? ''} value={c.id ?? ''} disabled={c.disabled} title={c.title}>
                                        {c.label}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                    {SELECTION_EXPORT_FORMATS.map(({format, label}) => (
                        <button
                            key={format}
                            type="button"
                            role="menuitem"
                            disabled={!enabled}
                            title={enabled ? undefined : reason ?? undefined}
                            onClick={() => void onExport(format)}
                            className="block w-full text-left text-[11px] text-white px-2 py-1 hover:bg-gray-600 disabled:opacity-40 disabled:hover:bg-transparent"
                        >
                            {label}
                        </button>
                    ))}
                </FloatingMenu>
            )}
            {/* The reason is on screen, not only in the tooltip: a tooltip does not exist on a
                touch screen, and "why is this greyed out" is the first question it raises. */}
            {!enabled && reason && <div className="text-[10px] opacity-70 mt-0.5 max-w-64">{reason}</div>}
            {note && <div className="text-[10px] text-amber-300 mt-0.5 max-w-64" title={note.title}>{note.text}</div>}
            {error && <div className="text-[10px] text-red-400 mt-0.5 max-w-64 break-words">{error}</div>}
        </div>
    );
};

/** The dropdown, portaled to the page so the panel's scroll area cannot clip it: below the button,
 *  or above it when there is no room below, kept inside the window. */
const FloatingMenu: React.FC<{
    anchorRef: React.RefObject<HTMLButtonElement | null>;
    onClose: () => void;
    children: React.ReactNode;
}> = ({anchorRef, onClose, children}) => {
    const menuRef = useRef<HTMLDivElement>(null);
    const [style, setStyle] = useState<React.CSSProperties>({visibility: 'hidden'});

    useLayoutEffect(() => {
        const place = () => {
            const rect = anchorRef.current?.getBoundingClientRect();
            const menu = menuRef.current;
            if (!rect || !menu) return;
            const w = menu.offsetWidth;
            const h = menu.offsetHeight;
            const left = Math.max(8, Math.min(rect.left, window.innerWidth - w - 8));
            let top = rect.bottom + 4;
            if (top + h > window.innerHeight - 8) top = Math.max(8, rect.top - h - 4);
            setStyle({top, left});
        };
        place();
        const outside = (e: Event) => {
            const t = e.target as Node | null;
            if (!t || menuRef.current?.contains(t) || anchorRef.current?.contains(t)) return;
            onClose();
        };
        const escape = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        document.addEventListener('mousedown', outside);
        document.addEventListener('touchstart', outside);
        document.addEventListener('keydown', escape);
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        return () => {
            document.removeEventListener('mousedown', outside);
            document.removeEventListener('touchstart', outside);
            document.removeEventListener('keydown', escape);
            window.removeEventListener('resize', place);
            window.removeEventListener('scroll', place, true);
        };
    }, [anchorRef, onClose]);

    return createPortal(
        <div
            ref={menuRef}
            role="menu"
            style={style}
            className="fixed z-[70] bg-gray-800 border border-gray-600 rounded-sm shadow-lg min-w-44"
        >
            {children}
        </div>,
        document.body,
    );
};

async function readLog(scope: string, derivedKey: string): Promise<GroupExportLog | null> {
    try {
        const bytes = await filesApi.getBlob(scope, `${derivedKey}.log.json`);
        return JSON.parse(new TextDecoder().decode(bytes)) as GroupExportLog;
    } catch {
        return null;
    }
}

const DownloadIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <path d="M8 2v8M4.5 6.5 8 10l3.5-3.5M2.5 13.5h11"/>
    </svg>
);

export default DownloadSelectionButton;
