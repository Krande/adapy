// ComponentControls — pick a registered ConnectionSpec, configure its
// inputs (sections, angles), and trigger an on-demand build whose
// resulting GLB the viewer renders. Mirror of SimulationControls'
// structural pattern: panel toggled from Menu, reads its UI state
// from a Zustand store, dispatches actions to a service-side
// pipeline.
//
// Stage 11 surface only: form + submit. Stage 13 hooks the produced
// GLB into the scene.

import React, {useEffect, useMemo, useRef, useState} from "react";

import {buildComponentViaServer} from "@/services/components/componentBuildPipeline";
import {
    type ComponentSpecManifestEntry,
    type ComponentSpecRoleSchema,
} from "@/services/viewerApi";
import {
    type ComponentBuildJob,
    useComponentBuildStore,
} from "@/state/componentBuildStore";
import {
    type ComponentInputs,
    useComponentControlsStore,
} from "@/state/componentControlsStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useComponentSpecsStore} from "@/state/componentSpecsStore";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";
import {useModelState} from "@/state/modelState";

// Specs are fetched centrally by componentSpecsStore (re-fetches on
// scope change, drives Menu button visibility). Builds always run in
// the user's currently-selected scope so the produced GLB lands where
// overlay_file_in_scene reads from. Empty cache → panel surfaces a
// "no specs published" hint.

const ComponentControls: React.FC = () => {
    const isVisible = useComponentControlsStore((s) => s.isVisible);
    const selectedSpecName = useComponentControlsStore((s) => s.selectedSpecName);
    const selectSpec = useComponentControlsStore((s) => s.selectSpec);
    const inputs = useComponentControlsStore((s) => s.inputs);
    const setRoleField = useComponentControlsStore((s) => s.setRoleField);
    const job = useComponentBuildStore((s) =>
        selectedSpecName ? s.jobs[selectedSpecName] ?? null : null,
    );
    const currentScope = useScopeStore((s) => s.current);

    const specs = useComponentSpecsStore((s) => s.specs);
    const loadError = useComponentSpecsStore((s) => s.loadError);
    const specsLoading = useComponentSpecsStore((s) => s.loading);
    const [submitting, setSubmitting] = useState(false);
    /** Last derivedKey we loaded into the scene — dedupe so the on-done
     *  effect doesn't keep re-fetching the same blob on every store
     *  re-render. */
    const loadedKeyRef = useRef<string | null>(null);

    // On build completion, overlay the produced GLB into the scene.
    // overlay_file_in_scene fetches the blob via authedFetch and routes
    // through the existing setupModelLoaderAsync path. Spec lineage
    // (spec_name, spec_inputs) lives in the bake's sibling
    // manifest.json — the panel reads it from componentSpecsStore
    // directly; the GLB no longer carries a duplicate copy in its
    // ADA extension.
    //
    // Drop any previously-loaded component overlays first: clicking
    // Build with new inputs (or a different spec) should REPLACE the
    // displayed component, not stack a new one on top of the old.
    // overlay_file_in_scene only de-dupes by sourceName, so a
    // sibling spec build under a different name would otherwise
    // remain visible. Walk every loadedSourceName with the
    // ``component:`` prefix and detach.
    useEffect(() => {
        if (!job || job.status !== "done" || !job.derivedKey) return;
        if (loadedKeyRef.current === job.derivedKey) return;
        loadedKeyRef.current = job.derivedKey;
        const sourceName = `component:${job.specName}`;
        const loaded = useModelState.getState().loadedSourceNames;
        for (const name of loaded) {
            if (name.startsWith("component:")) {
                unload_source_from_scene(name);
            }
        }
        void overlay_file_in_scene(sourceName, job.derivedKey).catch((err) => {
            console.error("component overlay failed", err);
        });
    }, [job?.status, job?.derivedKey, job?.specName]);

    const selectedEntry: ComponentSpecManifestEntry | null = useMemo(() => {
        if (!specs || !selectedSpecName) return null;
        return specs.specs[selectedSpecName] ?? null;
    }, [specs, selectedSpecName]);

    if (!isVisible) return null;

    const specNames = specs ? Object.keys(specs.specs).sort() : [];
    /** Group specs by the branch their manifest was published on so
     *  the dropdown surfaces lineage at a glance. Specs without a
     *  branch (legacy manifests from before the field was added) go
     *  under an empty-string key rendered as "(unknown branch)". */
    const specsByBranch: Record<string, string[]> = {};
    for (const name of specNames) {
        const branch = (specs?.specs[name]?.branch as string | undefined) ?? "";
        (specsByBranch[branch] ??= []).push(name);
    }
    const branchOrder = Object.keys(specsByBranch).sort();

    const handleSpecChange = (name: string) => {
        if (!name) {
            selectSpec(null);
            return;
        }
        const entry = specs?.specs[name];
        selectSpec(name, (entry?.defaults as ComponentInputs | undefined) ?? null);
        // Auto-load the bake's default preview GLB so the user sees
        // the connection immediately on selection. The bake's
        // ``preview_url`` is ``/api/scopes/<scope>/blobs/<key>``; the
        // blob-key portion is what overlay_file_in_scene wants, and
        // the scope override targets whichever scope the spec was
        // published in (often a project scope different from the
        // user's currently-active scope). Replaces the broken
        // "default preview" link that opened a raw bearer-less URL.
        const previewKey = entry?.preview_url?.match(/\/blobs\/(.+)$/)?.[1];
        if (previewKey && entry?.scope) {
            // Drop any prior component overlay first (different spec
            // means different component:<name> source key; without
            // this the previous one stays in the scene).
            const loaded = useModelState.getState().loadedSourceNames;
            for (const src of loaded) {
                if (src.startsWith("component:")) {
                    unload_source_from_scene(src);
                }
            }
            // Reset the loadedKeyRef so the on-build-done effect
            // doesn't skip a subsequent Build (it dedupes by
            // derivedKey).
            loadedKeyRef.current = null;
            void overlay_file_in_scene(
                `component:${name}`,
                previewKey,
                {scope: entry.scope},
            ).catch((err) => {
                console.warn("component default-preview overlay failed", err);
            });
        }
    };

    const handleBuild = async () => {
        if (!selectedSpecName) return;
        setSubmitting(true);
        try {
            await buildComponentViaServer(
                {
                    spec_name: selectedSpecName,
                    inputs,
                    // Route to the worker pool the bake declared in its
                    // manifest top-level capability. Undefined means
                    // "use the default pool" which only works for specs
                    // registered in the base worker (built-in adapy).
                    capability: selectedEntry?.capability,
                },
                {scope: scopeUrlPart(currentScope)},
            );
        } catch (err) {
            console.error("component build failed", err);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md min-w-[280px]">
            <div className="flex items-center gap-2">
                <span className="text-gray-300 shrink-0">Spec</span>
                <select
                    className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 min-w-0 flex-1 truncate"
                    value={selectedSpecName ?? ""}
                    onChange={(e) => handleSpecChange(e.target.value)}
                    disabled={specs === null}
                >
                    <option value="">
                        {specsLoading ? "loading…" : "— pick a connection spec —"}
                    </option>
                    {branchOrder.map((branch) => (
                        <optgroup
                            key={branch || "(unknown)"}
                            label={branch || "(unknown branch)"}
                        >
                            {specsByBranch[branch].map((name) => (
                                <option key={name} value={name}>
                                    {name}
                                </option>
                            ))}
                        </optgroup>
                    ))}
                </select>
            </div>

            {loadError && (
                <div className="text-red-400">Failed to load specs: {loadError}</div>
            )}
            {specs !== null && specNames.length === 0 && !loadError && !specsLoading && (
                <div className="text-gray-400">
                    No baked previews in this scope yet — run the
                    component-previews bake.
                </div>
            )}

            {selectedEntry && (
                <>
                    {selectedEntry.schema.roles.map((role) => (
                        <RoleRow
                            key={role.role}
                            role={role}
                            value={(inputs[role.role] ?? {}) as Record<string, unknown>}
                            onField={(field, value) => setRoleField(role.role, field, value)}
                        />
                    ))}
                    <div className="flex items-center gap-2 pt-1 border-t border-gray-700">
                        <button
                            type="button"
                            onClick={handleBuild}
                            disabled={submitting}
                            className="bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 text-white rounded-sm px-3 py-1"
                        >
                            {submitting ? "Building..." : "Build"}
                        </button>
                    </div>
                    {job && <JobStatus job={job} />}
                </>
            )}
        </div>
    );
};

const RoleRow: React.FC<{
    role: ComponentSpecRoleSchema;
    value: Record<string, unknown>;
    onField: (field: string, value: unknown) => void;
}> = ({role, value, onField}) => {
    const allowed = role.section_in?.join(" | ") ?? "(any)";
    const angleRange = role.angle_range;
    return (
        <div className="flex flex-col gap-1 p-1 bg-gray-800/40 rounded-sm">
            <div className="text-gray-300 font-medium">
                {role.role}
                {role.kind ? <span className="text-gray-500"> · {role.kind}</span> : null}
            </div>
            <label className="flex items-center gap-2">
                <span className="text-gray-400 w-12 shrink-0">section</span>
                <input
                    type="text"
                    className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 flex-1 min-w-0"
                    value={(value.section as string) ?? ""}
                    placeholder={allowed}
                    onChange={(e) => onField("section", e.target.value)}
                />
            </label>
            {angleRange && (
                <label className="flex items-center gap-2">
                    <span className="text-gray-400 w-12 shrink-0">angle°</span>
                    <input
                        type="range"
                        min={angleRange.min_deg}
                        max={angleRange.max_deg}
                        step={1}
                        value={typeof value.angle_deg === "number" ? value.angle_deg : angleRange.min_deg}
                        onChange={(e) => onField("angle_deg", parseFloat(e.target.value))}
                        className="flex-1 h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30 min-w-0"
                    />
                    <input
                        type="number"
                        className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 w-16"
                        min={angleRange.min_deg}
                        max={angleRange.max_deg}
                        value={typeof value.angle_deg === "number" ? value.angle_deg : ""}
                        onChange={(e) => onField("angle_deg", parseFloat(e.target.value))}
                    />
                </label>
            )}
        </div>
    );
};

const JobStatus: React.FC<{job: ComponentBuildJob}> = ({job}) => {
    const pct = Math.round((job.progress ?? 0) * 100);
    const color =
        job.status === "error" ? "text-red-400" :
        job.status === "done" ? "text-green-400" :
        "text-gray-300";
    return (
        <div className={`flex items-center gap-2 ${color}`}>
            <span className="font-mono w-12">{pct}%</span>
            <span className="truncate">{job.stage || job.status}</span>
            {job.error && <span className="text-red-400 truncate ml-2">{job.error}</span>}
        </div>
    );
};

export default ComponentControls;
