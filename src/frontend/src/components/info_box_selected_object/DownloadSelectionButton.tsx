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
import {pluginsApi} from '@/services/api/plugins';
import {assetProviderCollections} from '@/services/assetScopeCollections';
import {trackJob} from '@/services/jobTracking';
import {useConversionStore} from '@/state/conversionStore';
import {useMeStore} from '@/state/meStore';
import {
    SELECTION_EXPORT_FORMATS,
    SelectionExportError,
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
import {
    noGeometryQuestion,
    noProviderGeometryOf,
    requestProviderChoices,
    type NoProviderGeometry,
    type RequestProviderChoice,
} from '@/utils/export/missingGeometry';
import {requestUnmatchedMembers} from '@/utils/groups/groupRequest';
import type {GroupMember} from '@/utils/groups/savedGroups';

/** A failed group export the geometry provider had nothing for, waiting on the user: request the
 *  members' geometry (then export again), or not. */
interface MissingGeometryPrompt {
    readonly outcome: NoProviderGeometry;
    readonly format: SelectionExportFormat;
    /** The export as it was asked, so the re-run is the same export whatever is selected by then. */
    readonly members: readonly GroupMember[];
    readonly label: string;
    readonly choices: readonly RequestProviderChoice[];
    readonly chosen: string;
}

/**
 * "Download as…" for the selection, with everything under it, as STEP or IFC.
 *
 * One selected object or tree level goes through `planSelectionExport`. Several, or one read from
 * another provider than its own, are sent as a group and written as ONE file, each element under
 * the levels it sits in at its source. "Geometry from" chooses where node members are read from;
 * when part of the selection was delivered as a mesh it starts on a provider that publishes
 * objects, since a mesh has none to write.
 *
 * When that provider has published NOTHING for the members' collection yet, the export fails with
 * that as data (`noProviderGeometryOf`), and the panel asks whether to request the geometry -- the
 * members left out, from a provider's node request -- and exports again once it is published.
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
    const [missing, setMissing] = useState<MissingGeometryPrompt | null>(null);
    const [stage, setStage] = useState<string | null>(null);

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

    const runGroup = async (
        format: SelectionExportFormat,
        members: readonly GroupMember[],
        label: string,
        from: string | null,
    ) => {
        const out = await runExportBody(deps, scope, groupExportBody(members, label, format, from));
        const log = await readLog(scope, out.derivedKey);
        if (log?.warnings.length) {
            setNote({
                text: `${log.warnings.length} of ${log.members} left out`,
                title: log.warnings.join('\n'),
            });
        }
    };

    /** When the export failed because its geometry provider has published nothing for the members
     *  yet, ask whether to request it (`MissingGeometryPrompt`) instead of showing the error. False
     *  when it is any other failure, the server is too old to say, or nobody here can be asked. */
    const offerRequest = async (
        e: unknown,
        format: SelectionExportFormat,
        members: readonly GroupMember[],
        label: string,
    ): Promise<boolean> => {
        if (!(e instanceof SelectionExportError) || !e.derivedKey) return false;
        const outcome = noProviderGeometryOf(await readLog(scope, e.derivedKey), e.message);
        if (!outcome) return false;
        let choices: RequestProviderChoice[];
        try {
            const plugins = await pluginsApi.listBackendPlugins();
            choices = requestProviderChoices({
                outcome,
                declared: assetProviderCollections(plugins.plugins ?? []),
                readable: providers,
                isAdmin: useMeStore.getState().isAdmin,
                providerName,
            });
        } catch {
            return false;
        }
        if (choices.length === 0) return false;
        // The prompt says it instead: the job's toast would only repeat the error beside it.
        if (e.jobId) useConversionStore.getState().clearJob(`job:${e.jobId}`);
        setMissing({outcome, format, members, label, choices, chosen: choices[0].providerId});
        return true;
    };

    const onExport = async (format: SelectionExportFormat) => {
        if (!enabled || busy) return;
        setOpen(false);
        setBusy(format);
        setError(null);
        setNote(null);
        setMissing(null);
        const members = captured.members;
        const label = groupLabel;
        try {
            if (!useGroup && plan.ok) {
                await runExportBody(deps, scope, selectionExportBody(plan.request, format));
                return;
            }
            await runGroup(format, members, label, provider);
        } catch (e) {
            if (useGroup && (await offerRequest(e, format, members, label))) return;
            setError(describeExportError(e));
        } finally {
            setBusy(null);
        }
    };

    /** "Request": exactly the members left out, from the chosen provider -- the request the clash
     *  check makes for its missing members -- and, once it is published, the same export again. */
    const onRequest = async () => {
        const p = missing;
        if (!p || busy) return;
        const choice = p.choices.find((c) => c.providerId === p.chosen) ?? p.choices[0];
        setMissing(null);
        setError(null);
        setNote(null);
        setBusy(p.format);
        setStage(`requesting the geometry from ${choice.label}`);
        try {
            const {geometryRequestDeps} = await import('@/state/clashGeometryDeps');
            const requestDeps = await geometryRequestDeps(scope, (s) => setStage(s));
            const {notes, outcome} = await requestUnmatchedMembers(
                requestDeps,
                scope,
                choice.providerId,
                choice.req,
                p.outcome.unmatched,
                (s) => setStage(s),
            );
            if (!outcome || outcome.failed.length > 0 || outcome.published + outcome.unchanged === 0) {
                setError(notes.join(' ') || p.outcome.message);
                return;
            }
            setStage('exporting again');
            // No second prompt: a re-run that still finds nothing shows its error.
            await runGroup(p.format, p.members, p.label, choice.providerId);
        } catch (e) {
            setError(describeExportError(e));
        } finally {
            setBusy(null);
            setStage(null);
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
            {missing && (
                <MissingGeometryStep
                    prompt={missing}
                    onChoose={(id) => setMissing({...missing, chosen: id})}
                    onRequest={() => void onRequest()}
                    onCancel={() => {
                        setMissing(null);
                        setError(missing.outcome.message);
                    }}
                />
            )}
            {stage && <div className="text-[10px] text-gray-300 mt-0.5 max-w-64 truncate" title={stage}>{stage}…</div>}
            {note && <div className="text-[10px] text-amber-300 mt-0.5 max-w-64" title={note.title}>{note.text}</div>}
            {error && <div className="text-[10px] text-red-400 mt-0.5 max-w-64 break-words">{error}</div>}
        </div>
    );
};

/** The question when the geometry provider had nothing for the selection: which provider to ask
 *  (when more than one can be), Request or Cancel. Inline, as the clash check's missing-geometry
 *  step is, so the panel stays usable and the reasons can be read (on hover) before answering. */
const MissingGeometryStep: React.FC<{
    prompt: MissingGeometryPrompt;
    onChoose: (providerId: string) => void;
    onRequest: () => void;
    onCancel: () => void;
}> = ({prompt, onChoose, onRequest, onCancel}) => {
    const choice = prompt.choices.find((c) => c.providerId === prompt.chosen) ?? prompt.choices[0];
    const reasons = prompt.outcome.unmatched.map((u) => u.reason).filter(Boolean).join('\n');
    const btn = 'rounded-sm px-1.5 py-0.5 text-[11px] text-gray-100';
    return (
        <div
            role="alertdialog"
            aria-label="No stored geometry"
            className="mt-1 max-w-64 rounded-sm bg-amber-900/50 px-2 py-1 text-[11px] text-amber-100"
        >
            <div title={reasons || prompt.outcome.message}>{noGeometryQuestion(prompt.outcome, choice.label)}</div>
            <div className="text-[10px] opacity-80 mt-0.5">
                {prompt.outcome.members} member{prompt.outcome.members === 1 ? '' : 's'}; the export runs again once
                the geometry is published.
            </div>
            {prompt.choices.length > 1 && (
                <label className="mt-1 block text-[10px]">
                    Request from
                    <select
                        className="mt-0.5 block w-full rounded-sm bg-gray-700 text-[11px] text-white"
                        value={choice.providerId}
                        onChange={(e) => onChoose(e.target.value)}
                    >
                        {prompt.choices.map((c) => (
                            <option key={c.providerId} value={c.providerId}>
                                {c.label}
                            </option>
                        ))}
                    </select>
                </label>
            )}
            <div className="mt-1 flex flex-wrap justify-end gap-1">
                <button type="button" autoFocus className={`${btn} bg-blue-700 hover:bg-blue-600`} onClick={onRequest}>
                    Request
                </button>
                <button type="button" className={`${btn} bg-gray-700 hover:bg-gray-600`} onClick={onCancel}>
                    Cancel
                </button>
            </div>
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
