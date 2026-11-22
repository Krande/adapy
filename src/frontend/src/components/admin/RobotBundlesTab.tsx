import React, {useCallback, useEffect, useRef, useState} from "react";
import {
    allRefs,
    bundleFileProblem,
    exportRefs,
    formatBytes,
    pruneSelection,
    refusalProblems,
    resultSummary,
    robotSelection,
    ROBOT_BUNDLE_SUFFIX,
    planLabel,
    setAddOlderForAll,
    setRobotChoice,
    statusTone,
    toggleRef,
    toggleRobot,
    willWrite,
    writeCount,
    type RobotAssetList,
    type RobotAssetRow,
    type RobotImportChoices,
    type RobotImportPlanRow,
    type RobotImportPreview,
    type RobotImportResult,
} from "@/assets/robotBundles";
import {ApiError} from "@/services/api/client";
import {robotBundlesApi, saveBundle, type RobotBundleSelection} from "@/services/api/robotBundles";
import AdminTabShell from "./AdminTabShell";

// Admin -> Robot bundles: the provider store's robot assets, downloaded as one `.adabots` file (one
// robot, a selection, or all of them) and imported back from one. The rules are in
// docs/architecture/robot_asset_bundles.md; the routes in ada/comms/rest/routes/admin_robot_assets.py.
//
// Phone first: one column at every width below `sm`, 16 px gutters (`px-4`), every control at least
// 44 px tall on touch, long names wrapping (`break-words`, `min-w-0`) so nothing scrolls sideways.
// The file picker is a real <input type="file"> inside a <label> (what mobile browsers open), with
// no `accept` filter: iOS greys out a file whose type it does not know, and `.adabots` is not one it
// knows. The file is checked by its name once picked, and by the server in full.

/** Where the import is. */
export type ImportPhase =
    | {phase: "idle"}
    | {phase: "pick"; problem?: string | null}
    | {phase: "uploading"; fileName: string; sent: number}
    | {phase: "refused"; fileName: string; message: string; problems: string[]}
    | {phase: "preview"; fileName: string; preview: RobotImportPreview; choices: RobotImportChoices}
    | {phase: "importing"; fileName: string; preview: RobotImportPreview; choices: RobotImportChoices}
    | {phase: "done"; fileName: string; result: RobotImportResult};

const BTN =
    "inline-flex items-center justify-center gap-1.5 rounded-sm text-sm sm:text-xs px-4 py-2 sm:px-3 sm:py-1 " +
    "min-h-11 sm:min-h-0 focus:outline-hidden focus:ring-2 focus:ring-blue-400 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_PRIMARY = BTN + " bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white";
const BTN_PLAIN = BTN + " bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white";

const TONE: Record<string, string> = {
    good: "border-green-700 bg-green-900/40 text-green-200",
    info: "border-sky-700 bg-sky-900/40 text-sky-200",
    warn: "border-amber-600 bg-amber-900/40 text-amber-200",
    bad: "border-red-700 bg-red-900/40 text-red-200",
    muted: "border-gray-600 bg-gray-800 text-gray-300",
};

const Badge: React.FC<{tone: string; children: React.ReactNode; testId?: string}> = ({tone, children, testId}) => (
    <span
        className={"inline-block rounded-sm border px-1.5 py-px text-[11px] leading-5 break-words " + (TONE[tone] ?? TONE.muted)}
        data-testid={testId}
    >
        {children}
    </span>
);

/** A checkbox with a touch-sized hit area. */
const Check: React.FC<{
    checked: boolean;
    indeterminate?: boolean;
    onChange: () => void;
    label: string;
    disabled?: boolean;
    testId?: string;
}> = ({checked, indeterminate, onChange, label, disabled, testId}) => {
    const ref = useRef<HTMLInputElement>(null);
    useEffect(() => {
        if (ref.current) ref.current.indeterminate = !!indeterminate;
    }, [indeterminate]);
    return (
        <label className="inline-flex shrink-0 items-center justify-center min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 sm:p-1 cursor-pointer">
            <input
                ref={ref}
                type="checkbox"
                className="h-5 w-5 sm:h-4 sm:w-4 accent-blue-500"
                checked={checked}
                onChange={onChange}
                aria-label={label}
                aria-checked={indeterminate ? "mixed" : checked}
                disabled={disabled}
                data-testid={testId}
            />
        </label>
    );
};

// --- the list ----------------------------------------------------------------------------------------

const RobotCard: React.FC<{
    row: RobotAssetRow;
    selected: ReadonlySet<string>;
    onSelect: (next: Set<string>) => void;
}> = ({row, selected, onSelect}) => {
    const sel = robotSelection(selected, row);
    return (
        <li className="rounded-md border border-gray-700 bg-gray-900/40" data-robot-id={row.id}>
            <div className="flex items-start gap-2 px-2 sm:px-3 py-1">
                <Check
                    checked={sel === "all"}
                    indeterminate={sel === "some"}
                    onChange={() => onSelect(toggleRobot(selected, row))}
                    label={`Select every version of ${row.name}`}
                    testId={`select-robot-${row.id}`}
                />
                <div className="min-w-0 flex-1 py-2 sm:py-1">
                    <div className="text-sm font-medium text-gray-100 break-words">{row.name}</div>
                    <div className="text-xs text-gray-400 break-words">
                        <span className="break-all">{row.id}</span> · latest {row.latest} · {row.versions.length} version{row.versions.length === 1 ? "" : "s"}
                    </div>
                </div>
            </div>
            <ul className="border-t border-gray-800">
                {row.versions.map((v) => (
                    <li key={v.ref} className="flex items-center gap-2 px-2 sm:px-3 py-0.5 sm:pl-8" data-ref={v.ref}>
                        <Check
                            checked={selected.has(v.ref)}
                            onChange={() => onSelect(toggleRef(selected, v.ref))}
                            label={`Select ${v.ref}`}
                            testId={`select-${v.ref}`}
                        />
                        <div className="min-w-0 flex-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-gray-300">
                            <span className="font-mono text-gray-100 break-all">v{v.version}</span>
                            {v.latest && <Badge tone="good">latest</Badge>}
                            <span>{v.files} files</span>
                            <span>{formatBytes(v.bytes)}</span>
                            {v.problem && <span className="text-red-300 break-words">{v.problem}</span>}
                        </div>
                    </li>
                ))}
            </ul>
        </li>
    );
};

// --- the import panel --------------------------------------------------------------------------------

const DropZone: React.FC<{onFile: (f: File) => void; problem?: string | null; disabled?: boolean}> = ({
    onFile,
    problem,
    disabled,
}) => {
    const [over, setOver] = useState(false);
    return (
        <div>
            <label
                className={
                    "flex flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed px-4 py-6 text-center " +
                    "min-h-32 cursor-pointer select-none " +
                    (over ? "border-blue-400 bg-blue-900/30" : "border-gray-600 bg-gray-900/40 hover:border-gray-400")
                }
                onDragOver={(e) => {
                    e.preventDefault();
                    setOver(true);
                }}
                onDragLeave={() => setOver(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setOver(false);
                    const f = e.dataTransfer.files?.[0];
                    if (f && !disabled) onFile(f);
                }}
                data-testid="bundle-dropzone"
            >
                <span className="text-sm text-gray-100">
                    <span className="hidden sm:inline">Drop a {ROBOT_BUNDLE_SUFFIX} file here, or </span>
                    <span className="text-blue-300 underline">choose a bundle</span>
                </span>
                <span className="text-xs text-gray-400">Nothing is written before you have seen the preview.</span>
                <input
                    type="file"
                    className="sr-only"
                    disabled={disabled}
                    onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (f) onFile(f);
                    }}
                    data-testid="bundle-file-input"
                />
            </label>
            {problem && <div className="mt-2 text-xs text-red-300 break-words">{problem}</div>}
        </div>
    );
};

const Progress: React.FC<{value: number | null; label: string}> = ({value, label}) => (
    <div className="space-y-1" role="status" aria-live="polite">
        <div className="text-xs text-gray-300 break-words">{label}</div>
        <div
            className="h-2 w-full overflow-hidden rounded-full bg-gray-800"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value === null ? undefined : Math.round(value * 100)}
        >
            <div
                className={"h-full bg-blue-500 " + (value === null ? "w-1/3 animate-pulse" : "")}
                style={value === null ? undefined : {width: `${Math.round(value * 100)}%`}}
            />
        </div>
    </div>
);

const PlanRow: React.FC<{
    row: RobotImportPlanRow;
    choices: RobotImportChoices;
    onChoices: (c: RobotImportChoices) => void;
    disabled: boolean;
}> = ({row, choices, onChoices, disabled}) => {
    const mine = choices.robots?.[row.ref] ?? {};
    const writable = row.status === "new" || row.status === "newer" || row.status === "older";
    const included = mine.include !== false;
    const addOlder = mine.add_older ?? choices.add_older ?? false;
    return (
        <li className="rounded-md border border-gray-700 bg-gray-900/40 px-2 sm:px-3 py-1" data-plan-ref={row.ref} data-status={row.status}>
            <div className="flex items-start gap-2">
                {writable ? (
                    <Check
                        checked={willWrite(row, choices)}
                        onChange={() => {
                            if (row.status === "older") {
                                onChoices(setRobotChoice(setRobotChoice(choices, row.ref, "include", true), row.ref, "add_older", !addOlder));
                            } else {
                                onChoices(setRobotChoice(choices, row.ref, "include", !included));
                            }
                        }}
                        label={row.status === "older" ? `Add ${row.ref} as a past version` : `Import ${row.ref}`}
                        disabled={disabled}
                        testId={`plan-include-${row.ref}`}
                    />
                ) : (
                    <span className="inline-block min-w-11 sm:min-w-6" aria-hidden/>
                )}
                <div className="min-w-0 flex-1 py-2 sm:py-1 space-y-0.5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-sm text-gray-100 break-words min-w-0">{row.name}</span>
                        <Badge tone={statusTone(row.status)} testId={`plan-status-${row.ref}`}>
                            {planLabel(row, choices)}
                        </Badge>
                    </div>
                    <div className="text-xs text-gray-400 break-words">
                        <span className="break-all">{row.ref}</span> · {row.files} files · {formatBytes(row.bytes)}
                        {row.latest_before ? ` · stored latest ${row.latest_before}` : ""}
                    </div>
                    {row.detail && <div className="text-xs text-gray-300 break-words">{row.detail}</div>}
                    {row.status === "older" && (
                        <div className="text-xs text-sky-300">Tick to add it as a past version; the latest stays {row.latest_before}.</div>
                    )}
                    {row.duplicate_of && (
                        <div className="text-xs text-amber-300 break-words">The same robot.json is stored as {row.duplicate_of}.</div>
                    )}
                    {row.problems.length > 0 && (
                        <ul className="list-disc pl-4 text-xs text-red-300">
                            {row.problems.map((p) => (
                                <li key={p} className="break-words">{p}</li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>
        </li>
    );
};

export const ImportPanel: React.FC<{
    state: ImportPhase;
    onFile: (f: File) => void;
    onChoices: (c: RobotImportChoices) => void;
    onImport: () => void;
    onCancel: () => void;
}> = ({state, onFile, onChoices, onImport, onCancel}) => {
    if (state.phase === "idle") return null;
    return (
        <section className="rounded-md border border-blue-800 bg-gray-900/60 p-3 sm:p-4 space-y-3" data-testid="bundle-import" data-phase={state.phase}>
            <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-medium text-gray-100 flex-1 min-w-0">Import a robot bundle</h2>
                <button type="button" className={BTN_PLAIN} onClick={onCancel} disabled={state.phase === "importing"}>
                    {state.phase === "done" ? "Close" : "Cancel"}
                </button>
            </div>
            {state.phase === "pick" && <DropZone onFile={onFile} problem={state.problem}/>}
            {state.phase === "uploading" && (
                <Progress
                    value={state.sent < 1 ? state.sent : null}
                    label={state.sent < 1 ? `Uploading ${state.fileName}… ${Math.round(state.sent * 100)}%` : `Checking ${state.fileName}…`}
                />
            )}
            {state.phase === "refused" && (
                <div className="space-y-2" role="alert">
                    <div className="text-sm text-red-300 break-words">{state.fileName} was refused: {state.message}</div>
                    {state.problems.length > 0 && (
                        <ul className="list-disc pl-4 text-xs text-red-200 space-y-0.5">
                            {state.problems.map((p) => (
                                <li key={p} className="break-words">{p}</li>
                            ))}
                        </ul>
                    )}
                    <DropZone onFile={onFile}/>
                </div>
            )}
            {(state.phase === "preview" || state.phase === "importing") && (
                <PreviewBody state={state} onChoices={onChoices} onImport={onImport}/>
            )}
            {state.phase === "done" && <ResultBody fileName={state.fileName} result={state.result}/>}
        </section>
    );
};

const PreviewBody: React.FC<{
    state: Extract<ImportPhase, {phase: "preview" | "importing"}>;
    onChoices: (c: RobotImportChoices) => void;
    onImport: () => void;
}> = ({state, onChoices, onImport}) => {
    const {preview, choices} = state;
    const busy = state.phase === "importing";
    const n = writeCount(preview.robots, choices);
    const c = preview.counts;
    const older = c.older ?? 0;
    return (
        <div className="space-y-3">
            <div className="text-xs text-gray-300 break-words">
                {state.fileName}: {preview.bundle.robots} robot{preview.bundle.robots === 1 ? "" : "s"}
                {preview.bundle.exported_at ? `, exported ${preview.bundle.exported_at}` : ""}
                {preview.bundle.adapy_version ? ` by adapy ${preview.bundle.adapy_version}` : ""}.
            </div>
            <div className="flex flex-wrap gap-1.5" data-testid="plan-counts">
                {(["new", "newer", "unchanged", "older", "conflict", "invalid"] as const)
                    .filter((s) => (c[s] ?? 0) > 0)
                    .map((s) => (
                        <Badge key={s} tone={statusTone(s)}>
                            {c[s]} {s}
                        </Badge>
                    ))}
            </div>
            {older > 0 && (
                <label className="flex items-center gap-2 text-xs text-gray-200 min-h-11 sm:min-h-0">
                    <input
                        type="checkbox"
                        className="h-5 w-5 sm:h-4 sm:w-4 accent-blue-500"
                        checked={!!choices.add_older}
                        disabled={busy}
                        onChange={() => onChoices(setAddOlderForAll(choices, !choices.add_older))}
                        data-testid="plan-add-older"
                    />
                    Add every older version that is missing as a past version (the latest does not change)
                </label>
            )}
            <ul className="space-y-2">
                {preview.robots.map((r) => (
                    <PlanRow key={r.ref} row={r} choices={choices} onChoices={onChoices} disabled={busy}/>
                ))}
            </ul>
            <p className="text-xs text-gray-400 break-words">
                Imported robots go to the provider store only. No grant changes:{" "}
                {preview.grants.length
                    ? `${preview.grants.length} scope${preview.grants.length === 1 ? "" : "s"} with the robots grant read them at once.`
                    : "no scope holds the robots grant yet (Providers, Access)."}
            </p>
            {busy && <Progress value={null} label={`Importing ${n} robot${n === 1 ? "" : "s"}…`}/>}
            <div className="flex flex-col sm:flex-row gap-2">
                <button type="button" className={BTN_PRIMARY} onClick={onImport} disabled={busy || n === 0} data-testid="plan-import">
                    {n === 0 ? "Nothing to import" : `Import ${n} robot${n === 1 ? "" : "s"}`}
                </button>
            </div>
        </div>
    );
};

const ResultBody: React.FC<{fileName: string; result: RobotImportResult}> = ({fileName, result}) => (
    <div className="space-y-2" data-testid="import-results">
        <div className="text-sm text-gray-100 break-words" role="status">
            {fileName}: {resultSummary(result)}
        </div>
        <ul className="space-y-1">
            {result.results.map((r) => (
                <li key={r.ref} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs" data-result-ref={r.ref}>
                    <Badge tone={statusTone(r.result)}>{r.result}</Badge>
                    <span className="text-gray-100 break-all">{r.ref}</span>
                    <span className="text-gray-400 break-words basis-full sm:basis-auto">{r.detail}</span>
                </li>
            ))}
        </ul>
    </div>
);

// --- the tab -----------------------------------------------------------------------------------------

export interface RobotBundlesViewProps {
    list: RobotAssetList | null;
    loading: boolean;
    error: string | null;
    selected: ReadonlySet<string>;
    exporting: boolean;
    importState: ImportPhase;
    onRefresh: () => void;
    onSelect: (next: Set<string>) => void;
    onExport: (sel: RobotBundleSelection) => void;
    onStartImport: () => void;
    onFile: (f: File) => void;
    onChoices: (c: RobotImportChoices) => void;
    onImport: () => void;
    onCancelImport: () => void;
}

export const RobotBundlesView: React.FC<RobotBundlesViewProps> = (p) => {
    const rows = p.list?.robots ?? [];
    const refs = allRefs(rows);
    const allOn = refs.length > 0 && refs.every((r) => p.selected.has(r));
    const n = p.selected.size;
    return (
        <AdminTabShell
            header={
                <div className="flex flex-col gap-2 border-b border-gray-700 px-4 py-2 sm:flex-row sm:flex-wrap sm:items-center">
                    <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-gray-100">Robot bundles</div>
                        <div className="text-xs text-gray-400 break-words">
                            Download robot assets as one {ROBOT_BUNDLE_SUFFIX} file, or import one.
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
                        <button
                            type="button"
                            className={BTN_PRIMARY}
                            disabled={n === 0 || p.exporting}
                            onClick={() => p.onExport({refs: exportRefs(p.selected)})}
                            data-testid="download-selected"
                        >
                            Download selected{n ? ` (${n})` : ""}
                        </button>
                        <button
                            type="button"
                            className={BTN_PLAIN}
                            disabled={refs.length === 0 || p.exporting}
                            onClick={() => p.onExport({all: true})}
                            data-testid="download-all"
                        >
                            Download all
                        </button>
                        <button
                            type="button"
                            className={BTN_PLAIN + " col-span-2 sm:col-span-1"}
                            onClick={p.onStartImport}
                            disabled={p.importState.phase !== "idle" && p.importState.phase !== "done"}
                            data-testid="import-bundle"
                        >
                            Import bundle…
                        </button>
                        <button type="button" className={BTN_PLAIN + " col-span-2 sm:col-span-1"} onClick={p.onRefresh} aria-busy={p.loading}>
                            Refresh
                        </button>
                    </div>
                </div>
            }
            error={p.error}
            errorClassName="px-4 py-2 text-red-300 text-xs border-b border-gray-700 break-words"
            loading={p.loading && !p.list}
            bodyClassName="flex-1 min-h-0 overflow-y-auto overflow-x-hidden"
        >
            <div className="px-4 py-3 space-y-3 max-w-4xl" data-testid="robot-bundles">
                {p.exporting && <Progress value={null} label="Preparing the bundle…"/>}
                <ImportPanel
                    state={p.importState}
                    onFile={p.onFile}
                    onChoices={p.onChoices}
                    onImport={p.onImport}
                    onCancel={p.onCancelImport}
                />
                {p.list && (
                    <div className="text-xs text-gray-400 break-words">
                        {rows.length} robot{rows.length === 1 ? "" : "s"}, {refs.length} version{refs.length === 1 ? "" : "s"} in the provider
                        store ({p.list.provider} / {p.list.collection}).{" "}
                        {p.list.grants.length
                            ? `Read by ${p.list.grants.map((g) => g.target_scope).join(", ")}.`
                            : "No scope holds the robots grant."}
                    </div>
                )}
                {rows.length > 0 && (
                    <div className="flex items-center gap-2 text-xs text-gray-300">
                        <Check
                            checked={allOn}
                            indeterminate={!allOn && n > 0}
                            onChange={() => p.onSelect(allOn ? new Set() : new Set(refs))}
                            label="Select every version of every robot"
                            testId="select-all"
                        />
                        <span>{allOn ? "All selected" : n ? `${n} selected` : "Select all"}</span>
                    </div>
                )}
                {p.list && rows.length === 0 && (
                    <div className="text-sm text-gray-400">No robot assets are stored yet. Import a bundle to add some.</div>
                )}
                <ul className="space-y-2">
                    {rows.map((row) => (
                        <RobotCard key={row.id} row={row} selected={p.selected} onSelect={p.onSelect}/>
                    ))}
                </ul>
            </div>
        </AdminTabShell>
    );
};

const RobotBundlesTab: React.FC = () => {
    const [list, setList] = useState<RobotAssetList | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [exporting, setExporting] = useState(false);
    const [importState, setImportState] = useState<ImportPhase>({phase: "idle"});

    const refresh = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const next = await robotBundlesApi.list();
            setList(next);
            setSelected((s) => pruneSelection(s, next.robots));
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const onExport = async (sel: RobotBundleSelection) => {
        setExporting(true);
        setError(null);
        try {
            saveBundle(await robotBundlesApi.exportBundle(sel));
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setExporting(false);
        }
    };

    const onFile = async (file: File) => {
        const problem = bundleFileProblem(file, list?.bundle.limits);
        if (problem) {
            setImportState({phase: "pick", problem});
            return;
        }
        setImportState({phase: "uploading", fileName: file.name, sent: 0});
        try {
            const preview = await robotBundlesApi.previewImport(file, (sent) =>
                setImportState({phase: "uploading", fileName: file.name, sent}),
            );
            setImportState({phase: "preview", fileName: file.name, preview, choices: {}});
        } catch (e) {
            const detail = e instanceof ApiError ? e.detail : undefined;
            const problems = refusalProblems(detail);
            setImportState({phase: "refused", fileName: file.name, message: (e as Error).message, problems});
        }
    };

    const onImport = async () => {
        if (importState.phase !== "preview") return;
        const {fileName, preview, choices} = importState;
        setImportState({phase: "importing", fileName, preview, choices});
        try {
            const result = await robotBundlesApi.applyImport(preview.upload_id, choices);
            setImportState({phase: "done", fileName, result});
            void refresh();
        } catch (e) {
            setError((e as Error).message);
            setImportState({phase: "preview", fileName, preview, choices});
        }
    };

    const onCancelImport = () => {
        if (importState.phase === "preview") void robotBundlesApi.discardImport(importState.preview.upload_id);
        setImportState({phase: "idle"});
    };

    return (
        <RobotBundlesView
            list={list}
            loading={loading}
            error={error}
            selected={selected}
            exporting={exporting}
            importState={importState}
            onRefresh={() => void refresh()}
            onSelect={setSelected}
            onExport={(sel) => void onExport(sel)}
            onStartImport={() => setImportState({phase: "pick"})}
            onFile={(f) => void onFile(f)}
            onChoices={(choices) =>
                setImportState((s) => (s.phase === "preview" ? {...s, choices} : s))
            }
            onImport={() => void onImport()}
            onCancelImport={onCancelImport}
        />
    );
};

export default RobotBundlesTab;
