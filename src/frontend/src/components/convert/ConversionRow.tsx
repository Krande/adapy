import React, {useCallback, useEffect, useMemo, useState} from "react";
import {convertViaServer} from "@/services/conversion/serverPipeline";
import {viewerApi, TargetFormat} from "@/services/viewerApi";
import {runtime, ConversionOption} from "@/runtime/config";
import {useConversionStore} from "@/state/conversionStore";
import {useConvertPageStore, ConvertRow} from "@/state/convertPageStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {view_in_3d} from "@/utils/scene/handlers/view_in_3d";

type OptionValue = boolean | string | number | null;

// Render one per-job-knob widget driven by the @converter(options=...)
// schema. Type-dispatch is intentionally narrow — the wire format
// declares the shape, the worker enforces it, so the frontend only
// needs to map the four documented types to a sensible HTML input.
// Future option types can land here without touching ConversionRow's
// state plumbing.
const OptionWidget: React.FC<{
    opt: ConversionOption;
    value: OptionValue | undefined;
    onChange: (v: OptionValue) => void;
    disabled?: boolean;
}> = ({opt, value, onChange, disabled = false}) => {
    // Default to the schema's declared default whenever the user
    // hasn't picked one yet. Re-applying this on every render keeps
    // the input controlled without an extra useEffect.
    const effective = value === undefined ? opt.default ?? null : value;

    if (opt.type === "bool") {
        return (
            <label
                className="inline-flex items-center gap-1 text-xs text-gray-300 cursor-pointer"
                title={opt.description || opt.name}
            >
                <input
                    type="checkbox"
                    checked={effective === true}
                    onChange={(e) => onChange(e.target.checked)}
                    disabled={disabled}
                    className="accent-blue-500"
                />
                <span>{opt.name}</span>
            </label>
        );
    }
    if (opt.type === "enum" && opt.enum) {
        return (
            <label
                className="inline-flex items-center gap-1 text-xs text-gray-300"
                title={opt.description || opt.name}
            >
                <span>{opt.name}:</span>
                <select
                    className="bg-gray-900 border border-gray-600 rounded-sm px-1 py-0.5 text-xs text-gray-100"
                    value={String(effective ?? "")}
                    onChange={(e) => onChange(e.target.value)}
                    disabled={disabled}
                >
                    {opt.enum.map((v) => (
                        <option key={v} value={v}>{v}</option>
                    ))}
                </select>
            </label>
        );
    }
    if (opt.type === "int") {
        return (
            <label
                className="inline-flex items-center gap-1 text-xs text-gray-300"
                title={opt.description || opt.name}
            >
                <span>{opt.name}:</span>
                <input
                    type="number"
                    className="w-20 bg-gray-900 border border-gray-600 rounded-sm px-1 py-0.5 text-xs text-gray-100"
                    value={effective === null ? "" : String(effective)}
                    onChange={(e) => {
                        const n = parseInt(e.target.value, 10);
                        onChange(Number.isFinite(n) ? n : null);
                    }}
                    disabled={disabled}
                />
            </label>
        );
    }
    // string fallback (and unknown future types — best-effort).
    return (
        <label
            className="inline-flex items-center gap-1 text-xs text-gray-300"
            title={opt.description || opt.name}
        >
            <span>{opt.name}:</span>
            <input
                type="text"
                className="w-32 bg-gray-900 border border-gray-600 rounded-sm px-1 py-0.5 text-xs text-gray-100"
                value={effective === null ? "" : String(effective)}
                onChange={(e) => onChange(e.target.value || null)}
                disabled={disabled}
            />
        </label>
    );
};

// Per-file row on the /convert page. Owns the target-format pick, the
// "Convert" trigger, and the post-conversion download / view-in-3D
// buttons. Job lifecycle is read out of `useConversionStore` so the
// bottom-right toast (also subscribed to the same store) stays in
// sync when the user navigates away and comes back.

const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Converting",
    done: "Ready",
    error: "Failed",
};

function suggestedTarget(ext: string): TargetFormat {
    // Best-guess pick for the dropdown's default. Anything that isn't
    // already a .glb maps to .glb (matches the viewer's auto-convert
    // bias). .glb sources have no useful default target so we leave
    // them on .ifc — most users uploading a .glb to /convert want a
    // CAD interchange format out, not a no-op.
    if (ext === ".glb" || ext === ".gltf") return "ifc";
    return "glb";
}

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

function fmtSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

const ConversionRow: React.FC<{row: ConvertRow}> = ({row}) => {
    const setTarget = useConvertPageStore((s) => s.setTarget);
    const removeRow = useConvertPageStore((s) => s.removeRow);
    const current = useScopeStore((s) => s.current);
    const ext = useMemo(() => extOf(row.sourceKey), [row.sourceKey]);
    const [availableTargets, setAvailableTargets] = useState<TargetFormat[]>([]);
    const [submitting, setSubmitting] = useState(false);
    // Per-option user picks. Keyed by option name; undefined means
    // "the user hasn't touched this knob, use the schema default".
    // Reset whenever ``target`` changes since the option schema is
    // (from, to)-specific.
    const [optionValues, setOptionValues] = useState<Record<string, OptionValue>>({});

    // Use the row's `target` if set, else the best-guess default.
    const target: TargetFormat = row.target ?? suggestedTarget(ext);

    // Job state for the current (source, target) pair lives in the
    // shared conversion store under the same compound key
    // `convertViaServer` uses internally. Re-read on every render so
    // the row picks up progress updates without a manual subscription.
    const storeKey = `${row.sourceKey}::${target}`;
    const job = useConversionStore((s) => s.jobs[storeKey]);
    const clearJob = useConversionStore((s) => s.clearJob);

    // Ask the server which targets are viable for this source — the
    // backend already has this endpoint and it's the authoritative
    // answer (cheaper to call once per row than to mirror the mapping
    // client-side and drift). Falls back silently to the static
    // TargetFormat union if the call fails.
    useEffect(() => {
        if (!current) return;
        let cancelled = false;
        (async () => {
            try {
                const t = await viewerApi.convertTargets(
                    scopeUrlPart(current), row.sourceKey,
                );
                if (!cancelled && t.length > 0) setAvailableTargets(t);
            } catch {
                /* leave availableTargets empty; dropdown shows the union below */
            }
        })();
        return () => { cancelled = true; };
    }, [current, row.sourceKey]);

    // Reset option picks when the target changes — the schema is
    // (from, to)-specific, so old keys may not be valid against the
    // new target's option set.
    useEffect(() => {
        setOptionValues({});
    }, [target]);

    const onConvert = useCallback(async () => {
        if (!current) return;
        setSubmitting(true);
        try {
            // Drop ``undefined`` entries — convertViaServer / the
            // API both treat presence as "explicit override", and
            // we only want to send keys the user actually touched
            // (or that have a non-default schema value).
            const conversionOptions: Record<string, OptionValue> = {};
            for (const [k, v] of Object.entries(optionValues)) {
                if (v !== undefined) conversionOptions[k] = v;
            }
            await convertViaServer(
                scopeUrlPart(current), row.sourceKey, target,
                Object.keys(conversionOptions).length
                    ? {conversionOptions}
                    : undefined,
            );
        } catch (e) {
            // convertViaServer already writes the error to the
            // conversion store, which the row reads below — no extra
            // surface here. Logged for the dev console.
            // eslint-disable-next-line no-console
            console.warn("[convert]", e);
        } finally {
            setSubmitting(false);
        }
    }, [current, row.sourceKey, target, optionValues]);

    const onDownload = useCallback(async () => {
        if (!current || !job?.derivedKey) return;
        const baseName = row.sourceKey.replace(/\.[^.]+$/, "");
        const suggested = `${baseName}.${target}`;
        await viewerApi.downloadBlob(scopeUrlPart(current), job.derivedKey, suggested);
    }, [current, job?.derivedKey, row.sourceKey, target]);

    const onViewIn3D = useCallback(() => {
        if (!current || !job?.derivedKey) return;
        // Modal mode loads in the underlying scene; path-mode opens
        // a new tab via ``?scope&file&derived`` for useUrlParamLoad.
        void view_in_3d(row.sourceKey, job.derivedKey);
    }, [current, job?.derivedKey, row.sourceKey]);

    const onRemove = useCallback(() => {
        if (job) clearJob(storeKey);
        removeRow(row.sourceKey);
    }, [clearJob, job, removeRow, row.sourceKey, storeKey]);

    // Target options come from three layered sources, picked in order:
    //   1. The /scopes/{scope}/convert/targets endpoint — authoritative
    //      per-source answer (the server consults supported_targets_for
    //      which reads the registry).
    //   2. runtime.conversionTargetsFor(ext) — merged worker matrix
    //      from /api/config. Works without a network round-trip and
    //      stays right even when (1) hasn't returned yet.
    //   3. The static union ``glb / ifc / xml`` — last-resort fallback
    //      for the desktop bundle or a deployment where no worker has
    //      registered yet.
    const matrixTargets = runtime.conversionTargetsFor(ext);
    const targetOptions: TargetFormat[] = availableTargets.length > 0
        ? availableTargets
        : matrixTargets.length > 0
            ? (matrixTargets as TargetFormat[])
            : ["glb", "ifc", "xml"];

    // Per-(from, target) option schema. Pulled from the merged
    // worker matrix; empty list when the pair has no per-job knobs,
    // in which case the option panel below collapses to nothing.
    const optionSchema = runtime.conversionOptionsFor(ext, target);

    const isRunning = job?.status === "queued" || job?.status === "running";
    const isDone = job?.status === "done";
    const isError = job?.status === "error";
    const pct = Math.round((job?.progress || 0) * 100);
    const canPreview = isDone && (target === "glb" || job?.derivedKey?.endsWith(".glb") || job?.derivedKey?.endsWith(".gltf"));

    return (
        <div className="rounded-md border border-gray-700 bg-gray-800/60 p-3 space-y-2">
            <div className="flex justify-between items-start gap-3">
                <div className="min-w-0 flex-1">
                    <div className="font-mono text-sm truncate">{row.sourceKey}</div>
                    <div className="text-[11px] text-gray-400">{fmtSize(row.sizeBytes)}</div>
                </div>
                <button
                    type="button"
                    onClick={onRemove}
                    className="shrink-0 inline-flex items-center justify-center w-7 h-7 rounded-sm border border-gray-600 bg-gray-700/60 text-gray-200 hover:bg-gray-600"
                    aria-label={`Remove ${row.sourceKey}`}
                    title="Remove from list"
                >
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                        <path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none"/>
                    </svg>
                </button>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
                <label className="text-xs text-gray-400">convert to:</label>
                <select
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                    value={target}
                    onChange={(e) => setTarget(row.sourceKey, e.target.value as TargetFormat)}
                    disabled={isRunning || submitting}
                >
                    {targetOptions.map((t) => (
                        <option key={t} value={t}>.{t}</option>
                    ))}
                </select>

                {!isDone && (
                    <button
                        type="button"
                        onClick={onConvert}
                        disabled={isRunning || submitting}
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm"
                    >
                        {isRunning ? "Converting…" : submitting ? "Submitting…" : "Convert"}
                    </button>
                )}
                {isDone && (
                    <>
                        <button
                            type="button"
                            onClick={onDownload}
                            className="bg-emerald-700 hover:bg-emerald-600 text-white text-sm px-3 py-1 rounded-sm"
                        >
                            Download
                        </button>
                        {canPreview && (
                            <button
                                type="button"
                                onClick={onViewIn3D}
                                className="bg-gray-700 hover:bg-gray-600 text-gray-100 text-sm px-3 py-1 rounded-sm"
                            >
                                View in 3D ↗
                            </button>
                        )}
                    </>
                )}
            </div>

            {optionSchema.length > 0 && !isDone && (
                <div className="flex items-center gap-4 flex-wrap pt-1 border-t border-gray-700/50">
                    <span className="text-[11px] uppercase tracking-wider text-gray-500">
                        options
                    </span>
                    {optionSchema.map((opt) => (
                        <OptionWidget
                            key={opt.name}
                            opt={opt}
                            value={optionValues[opt.name]}
                            onChange={(v) => setOptionValues((prev) => ({...prev, [opt.name]: v}))}
                            disabled={isRunning || submitting}
                        />
                    ))}
                </div>
            )}

            {job && (
                <div className="space-y-1">
                    <div className="flex justify-between text-[11px] text-gray-400">
                        <span>{STATUS_LABEL[job.status] || job.status}{job.stage ? ` — ${job.stage}` : ""}</span>
                        {!isError && <span>{pct}%</span>}
                    </div>
                    {!isError && (
                        <div className="h-1 bg-gray-700 rounded-sm overflow-hidden">
                            <div
                                className={
                                    "h-full transition-all " +
                                    (isDone ? "bg-emerald-500" : "bg-blue-500")
                                }
                                style={{width: `${Math.max(pct, isDone ? 100 : 4)}%`}}
                            />
                        </div>
                    )}
                    {isError && (
                        <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-32 overflow-auto m-0">
                            {job.error || "(no error message)"}
                        </pre>
                    )}
                </div>
            )}
        </div>
    );
};

export default ConversionRow;
