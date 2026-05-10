import React, {useEffect, useMemo, useState} from "react";

import {
    FeaManifest,
    FeaManifestField,
    ScopeUrl,
    viewerApi,
} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

interface FeaStreamingPickerModalProps {
    sourceName: string;
    onClose: () => void;
}

/**
 * Streaming-viewer field/step/component picker.
 *
 * Fetches the per-source manifest from /fea/manifest, then renders
 * a (field, component, step) selection UI populated entirely from
 * the manifest. Picker defaults — magnitude reduction for vector
 * fields, viridis colormap, fixed colour range across steps — are
 * baked into the manifest server-side; the modal just reads them.
 *
 * Phase 1 step 3: this is the picker UI only. Selection currently
 * console-logs the chosen (field, component, step). The shader
 * pipeline that consumes that selection lands in step 4.
 */
const FeaStreamingPickerModal: React.FC<FeaStreamingPickerModalProps> = ({
    sourceName,
    onClose,
}) => {
    const scope: ScopeUrl = scopeUrlPart(useScopeStore.getState().current);

    const [manifest, setManifest] = useState<FeaManifest | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [copied, setCopied] = useState<boolean>(false);

    const [fieldName, setFieldName] = useState<string | null>(null);
    const [stepIndex, setStepIndex] = useState<number>(0);
    const [reduction, setReduction] = useState<string>("magnitude");

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const m = await viewerApi.feaManifest(scope, sourceName);
                if (cancelled) return;
                setManifest(m);
                if (m.fields.length > 0) {
                    const first = m.fields[0];
                    setFieldName(first.name_canonical);
                    setReduction(first.default_view.reduction);
                    setStepIndex(0);
                }
            } catch (err) {
                if (cancelled) return;
                setLoadError(err instanceof Error ? err.message : String(err));
            } finally {
                if (!cancelled) setLoading(false);
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

    const activeField: FeaManifestField | null = useMemo(() => {
        if (!manifest || !fieldName) return null;
        return (
            manifest.fields.find((f) => f.name_canonical === fieldName) ?? null
        );
    }, [manifest, fieldName]);

    // Component dropdown options: magnitude (only for vector fields)
    // plus each component name. The default is taken from the
    // field's default_view, which the bake pre-computed.
    const reductionOptions = useMemo<string[]>(() => {
        if (!activeField) return [];
        const out: string[] = [];
        if (activeField.kind.startsWith("vector")) {
            out.push("magnitude");
        }
        for (const c of activeField.components) out.push(c);
        return out;
    }, [activeField]);

    const activeStep = useMemo(() => {
        if (!activeField) return null;
        if (stepIndex < 0 || stepIndex >= activeField.steps.length) return null;
        return activeField.steps[stepIndex];
    }, [activeField, stepIndex]);

    // When the field changes, snap the reduction back to that field's
    // default and clamp the step slider to its step count. Without
    // this, switching from a 50-step eigen field to a 1-step static
    // field with stepIndex=42 would render an out-of-range slider.
    useEffect(() => {
        if (!activeField) return;
        if (!reductionOptions.includes(reduction)) {
            setReduction(activeField.default_view.reduction);
        }
        if (stepIndex >= activeField.steps.length) {
            setStepIndex(0);
        }
        // Intentionally not tracking `reduction` / `stepIndex` here —
        // they're the values we're conditionally repairing.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeField, reductionOptions]);

    const onApply = () => {
        if (!activeField || !activeStep) return;
        // Step 4 wires this into the shader / step scrubber. For now,
        // log so the picker is exerciseable end-to-end against a real
        // backend without rendering plumbing.
        // eslint-disable-next-line no-console
        console.log("[fea-streaming-picker] selection", {
            source: sourceName,
            field: activeField.name_canonical,
            field_native: activeField.name_native,
            step_index: stepIndex,
            step_value: activeStep.value,
            reduction,
            colormap: activeField.default_view.colormap,
        });
        onClose();
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
                            FEA streaming viewer
                        </h3>
                        <p className="text-xs text-gray-300 truncate">
                            {sourceName}
                        </p>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-gray-300 hover:text-white text-lg leading-none p-1"
                        aria-label="Close"
                    >
                        ×
                    </button>
                </div>

                {loading && (
                    <div className="text-sm text-gray-300 py-4 text-center">
                        Baking artefacts…
                    </div>
                )}

                {loadError && (
                    <div className="flex flex-col gap-1 py-2">
                        <div className="text-sm text-red-300 break-words">
                            Couldn't load this file:
                        </div>
                        <pre className="text-red-400 break-all whitespace-pre-wrap font-mono text-[11px] leading-snug max-h-64 overflow-auto m-0">
                            {loadError}
                        </pre>
                        <div className="flex justify-end">
                            <button
                                type="button"
                                onClick={async () => {
                                    try {
                                        await navigator.clipboard.writeText(
                                            `${sourceName}\n${loadError}`,
                                        );
                                        setCopied(true);
                                        setTimeout(() => setCopied(false), 1500);
                                    } catch {
                                        /* clipboard blocked — user can still select-and-copy */
                                    }
                                }}
                                className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-2 py-0.5 rounded text-[11px]"
                                title="Copy error to clipboard"
                            >
                                {copied ? "Copied" : "Copy"}
                            </button>
                        </div>
                    </div>
                )}

                {manifest && !loading && !loadError && (
                    <div className="space-y-3">
                        <label className="block text-xs">
                            <span className="block mb-1 text-gray-300">Field</span>
                            <select
                                className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1"
                                value={fieldName ?? ""}
                                onChange={(e) => setFieldName(e.target.value)}
                            >
                                {manifest.fields.map((f) => (
                                    <option
                                        key={f.name_canonical}
                                        value={f.name_canonical}
                                    >
                                        {f.name_canonical}
                                        {f.name_native &&
                                        f.name_native !== f.name_canonical
                                            ? ` (${f.name_native})`
                                            : ""}
                                    </option>
                                ))}
                            </select>
                        </label>

                        {activeField && reductionOptions.length > 1 && (
                            <label className="block text-xs">
                                <span className="block mb-1 text-gray-300">
                                    Component
                                </span>
                                <select
                                    className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1"
                                    value={reduction}
                                    onChange={(e) => setReduction(e.target.value)}
                                >
                                    {reductionOptions.map((opt) => (
                                        <option key={opt} value={opt}>
                                            {opt}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        )}

                        {activeField && activeField.steps.length > 0 && (
                            <label className="block text-xs">
                                <span className="block mb-1 text-gray-300">
                                    Step{" "}
                                    <span className="text-gray-500">
                                        {stepIndex + 1} / {activeField.steps.length}
                                    </span>
                                </span>
                                {activeField.steps.length > 1 ? (
                                    <input
                                        type="range"
                                        min={0}
                                        max={activeField.steps.length - 1}
                                        value={stepIndex}
                                        onChange={(e) =>
                                            setStepIndex(Number(e.target.value))
                                        }
                                        className="w-full"
                                    />
                                ) : (
                                    <div className="text-gray-300 px-2 py-1 bg-gray-700 border border-gray-600 rounded">
                                        single step
                                    </div>
                                )}
                                {activeStep && (
                                    <div className="mt-1 text-gray-400 font-mono">
                                        value = {activeStep.value}
                                    </div>
                                )}
                            </label>
                        )}

                        <div className="flex justify-end gap-2 pt-1">
                            <button
                                onClick={onClose}
                                className="px-3 py-1 text-xs rounded bg-gray-600 hover:bg-gray-500"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={onApply}
                                className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-60"
                                disabled={!activeField || !activeStep}
                            >
                                Apply
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default FeaStreamingPickerModal;
