import React, {useCallback, useEffect, useState} from "react";
import {ApiError, viewerApi, type ProceduralDefaultEngine, type ProceduralEngineSummary} from "@/services/viewerApi";
import {PROCEDURAL_DEFAULT_ENGINE_SETTING} from "@/services/api/proceduralCatalog";

// The deployment's default procedural engine: the one a new Builder model gets when nobody
// names one. The deployment sets it (ADA_DEFAULT_PROCEDURAL_ENGINE); this row overrides it
// without a redeploy, and shows what is in effect -- including that a configured engine is not
// offered right now and the built-in stands in for it. Stored models keep their own engine.

/** What the row says is in effect, from the server's `default_engine`. */
export function describeDefaultEngine(d: ProceduralDefaultEngine | null): string {
    if (!d) return "this server does not report a default engine";
    const where = d.source === "setting" ? "this setting" : d.source === "env" ? "the deployment (ADA_DEFAULT_PROCEDURAL_ENGINE)" : "adapy's built-in";
    return d.fallback ? `${d.name} (${d.slug}) -- ${d.note ?? "the configured engine is not available"}` : `${d.name} (${d.slug}), from ${where}`;
}

export const ProceduralDefaultEngineSetting: React.FC<{onError: (msg: string | null) => void; scope?: string}> = ({
    onError,
    scope = "shared",
}) => {
    const [value, setValue] = useState("");
    const [engines, setEngines] = useState<ProceduralEngineSummary[]>([]);
    const [effective, setEffective] = useState<ProceduralDefaultEngine | null>(null);
    const [saving, setSaving] = useState(false);

    const refresh = useCallback(async () => {
        try {
            const [v, list, d] = await Promise.all([
                viewerApi.adminGetSetting(PROCEDURAL_DEFAULT_ENGINE_SETTING),
                viewerApi.listProceduralEngines(scope),
                viewerApi.proceduralDefaultEngine(scope),
            ]);
            setValue((v || "").trim());
            setEngines(list);
            setEffective(d);
        } catch {
            /* surfaced by the tab's batch load */
        }
    }, [scope]);
    useEffect(() => {
        void refresh();
    }, [refresh]);

    const save = async (next: string) => {
        setSaving(true);
        onError(null);
        try {
            await viewerApi.adminSetSetting(PROCEDURAL_DEFAULT_ENGINE_SETTING, next);
            setValue(next);
            await refresh();
        } catch (e) {
            onError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setSaving(false);
        }
    };

    // A configured value no engine answers to stays selectable, so the row shows it as it is.
    const offered = engines.some((e) => e.slug === value || e.id === value);
    return (
        <div className="px-3 sm:px-4 py-3 border-b border-gray-800 space-y-2" data-testid="procedural-default-engine">
            <div>
                <div className="font-medium text-sm">Default procedural engine</div>
                <div className="text-[11px] text-gray-400 font-mono">{PROCEDURAL_DEFAULT_ENGINE_SETTING}</div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
                <select
                    value={value}
                    disabled={saving}
                    onChange={(e) => void save(e.target.value)}
                    className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-1 text-sm w-64 text-gray-100"
                >
                    <option value="">Deployment default</option>
                    {value && !offered && <option value={value}>{value} (not available)</option>}
                    {engines.map((e) => (
                        <option key={e.id} value={e.slug}>
                            {e.name} ({e.slug})
                        </option>
                    ))}
                </select>
                {saving && <span className="text-[11px] text-gray-400">Saving…</span>}
            </div>
            <div className={`text-xs max-w-2xl ${effective?.fallback ? "text-amber-400" : "text-gray-400"}`} data-testid="procedural-default-engine-effective">
                In effect: {describeDefaultEngine(effective)}
            </div>
            <div className="text-xs text-gray-400 max-w-2xl">
                The engine a new procedural model gets when nobody names one (a template keeps its own). Empty uses
                the deployment&apos;s ADA_DEFAULT_PROCEDURAL_ENGINE, else adapy&apos;s built-in. An engine that is not
                offered (its worker is down, say) falls back to the built-in. Existing models keep their engine.
            </div>
        </div>
    );
};
