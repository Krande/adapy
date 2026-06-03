import React, {useEffect, useMemo, useState} from "react";
import {viewerApi, ResultMeta, ScopeUrl} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {view_picked_fea_render} from "@/utils/scene/handlers/view_picked_fea_render";

interface FieldPickerModalProps {
    sourceName: string;
    onClose: () => void;
}

/**
 * Modal that lets the user pick (step, field) for a FEA result file
 * and re-render the GLB with that pair. Server-side caches each
 * picked combo so re-opening the same selection is instant.
 *
 * Layout matches the audit log error modal: full-screen overlay,
 * Esc-to-close, click-outside-to-close. Two <select> dropdowns +
 * View / Cancel buttons.
 */
const FieldPickerModal: React.FC<FieldPickerModalProps> = ({sourceName, onClose}) => {
    const scope: ScopeUrl = scopeUrlPart(useScopeStore.getState().current);

    const [meta, setMeta] = useState<ResultMeta | null>(null);
    const [loadingMeta, setLoadingMeta] = useState<boolean>(true);
    const [metaError, setMetaError] = useState<string | null>(null);

    const [step, setStep] = useState<number | null>(null);
    const [field, setField] = useState<string | null>(null);

    const [submitting, setSubmitting] = useState<boolean>(false);
    const [submitError, setSubmitError] = useState<string | null>(null);
    const [copied, setCopied] = useState<boolean>(false);

    const onCopyError = async (text: string) => {
        try {
            await navigator.clipboard.writeText(`${sourceName}\n${text}`);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy */
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const m = await viewerApi.resultMeta(scope, sourceName);
                if (cancelled) return;
                setMeta(m);
                setStep(m.default_step);
                setField(m.default_field);
            } catch (err) {
                if (cancelled) return;
                setMetaError(err instanceof Error ? err.message : String(err));
            } finally {
                if (!cancelled) setLoadingMeta(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [scope, sourceName]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    // Steps available for the currently picked field. Most SIFs have
    // every field at every step, but the meta payload reports per-field
    // step lists so we can disable invalid combinations cleanly.
    const fieldEntry = useMemo(
        () => meta?.fields.find((f) => f.name === field) ?? null,
        [meta, field],
    );
    const stepsForField: number[] = fieldEntry?.steps ?? meta?.steps ?? [];

    const onView = async () => {
        if (step === null || field === null) return;
        setSubmitting(true);
        setSubmitError(null);
        try {
            await view_picked_fea_render(sourceName, step, field);
            onClose();
        } catch (err) {
            setSubmitError(err instanceof Error ? err.message : String(err));
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onClick={onClose}
        >
            <div
                className="bg-gray-800 text-white rounded-lg shadow-xl w-full max-w-md p-4"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex justify-between items-start mb-3">
                    <div className="min-w-0">
                        <h3 className="font-bold truncate" title={sourceName}>
                            Select field
                        </h3>
                        <p className="text-xs text-gray-300 truncate">{sourceName}</p>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-gray-300 hover:text-white text-lg leading-none p-1"
                        aria-label="Close"
                    >
                        ×
                    </button>
                </div>

                {loadingMeta && (
                    <div className="text-sm text-gray-300 py-4 text-center">
                        Reading result file…
                    </div>
                )}

                {metaError && (
                    <div className="flex flex-col gap-1 py-2">
                        <div className="text-sm text-red-300 wrap-break-word">
                            Couldn't read this file:
                        </div>
                        <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-64 overflow-auto m-0">
                            {metaError}
                        </pre>
                        <div className="flex justify-end">
                            <button
                                type="button"
                                onClick={() => void onCopyError(metaError)}
                                className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm text-[11px]"
                                title="Copy error to clipboard"
                            >
                                {copied ? "Copied" : "Copy"}
                            </button>
                        </div>
                    </div>
                )}

                {meta && !loadingMeta && (
                    <div className="space-y-3">
                        <label className="block text-xs">
                            <span className="block mb-1 text-gray-300">Field</span>
                            <select
                                className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1"
                                value={field ?? ""}
                                onChange={(e) => setField(e.target.value)}
                            >
                                {meta.fields.map((f) => (
                                    <option key={f.name} value={f.name}>
                                        {f.name}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="block text-xs">
                            <span className="block mb-1 text-gray-300">Step</span>
                            <select
                                className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1"
                                value={step ?? ""}
                                onChange={(e) => setStep(Number(e.target.value))}
                            >
                                {stepsForField.map((s) => (
                                    <option key={s} value={s}>
                                        {s}
                                    </option>
                                ))}
                            </select>
                        </label>
                        {submitError && (
                            <div className="flex flex-col gap-1">
                                <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-40 overflow-auto m-0">
                                    {submitError}
                                </pre>
                                <div className="flex justify-end">
                                    <button
                                        type="button"
                                        onClick={() => void onCopyError(submitError)}
                                        className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded-sm text-[11px]"
                                        title="Copy error to clipboard"
                                    >
                                        {copied ? "Copied" : "Copy"}
                                    </button>
                                </div>
                            </div>
                        )}
                        <div className="flex justify-end gap-2 pt-1">
                            <button
                                onClick={onClose}
                                className="px-3 py-1 text-xs rounded-sm bg-gray-600 hover:bg-gray-500"
                                disabled={submitting}
                            >
                                Cancel
                            </button>
                            <button
                                onClick={onView}
                                className="px-3 py-1 text-xs rounded-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-60"
                                disabled={submitting || step === null || field === null}
                            >
                                {submitting ? "Loading…" : "View"}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default FieldPickerModal;
