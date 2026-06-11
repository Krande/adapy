import React from "react";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {useModelState, loadedSourceGroups} from "@/state/modelState";

// Per-model aggregate baked into ``DesignDataExtension.stats`` and
// ``SimulationDataExtensionMetadata.stats`` at GLB time. The schema
// also tolerates `total_mass` / `total_volume` as optional, so we
// loosen the shape here rather than reuse the generated COG types
// (the json2ts output renames the duplicate definitions to
// ``COG1`` / ``COG2`` which is awkward to import by name).
interface Cog {
    x: number;
    y: number;
    z: number;
    total_mass?: number;
    total_volume?: number;
}

interface ModelStats {
    key: string;
    name: string;
    kind: "CAD" | "FEA";
    primaryCog?: { label: string } & Cog;
    secondaryCog?: { label: string } & Cog;
    counts: Record<string, number>;
}

interface SourceStats {
    /** Storage key of the loaded file the stats came from. */
    source: string;
    models: ModelStats[];
}

function statsFromExtension(ext: any): ModelStats[] {
    const items: ModelStats[] = [];

    for (const sim of ext?.simulation_objects ?? []) {
        const stats = sim.stats;
        if (!stats) continue;
        items.push({
            key: `fea::${sim.name}`,
            name: sim.name || "Unnamed",
            kind: "FEA",
            primaryCog: stats.cog ? {label: "COG", ...(stats.cog as Cog)} : undefined,
            counts: (stats.element_counts ?? {}) as Record<string, number>,
        });
    }

    for (const d of ext?.design_objects ?? []) {
        const stats = d.stats;
        if (!stats) continue;
        // cog_mass is the honest aggregate when available; cog_volume
        // is always present whenever any geometry contributes volume.
        // Prefer mass as primary and show volume as secondary; fall
        // back to volume-as-primary when mass isn't baked.
        const m: ModelStats = {
            key: `cad::${d.name}`,
            name: d.name || "Unnamed",
            kind: "CAD",
            counts: (stats.object_counts ?? {}) as Record<string, number>,
        };
        if (stats.cog_mass) {
            m.primaryCog = {label: "COG (mass)", ...(stats.cog_mass as Cog)};
            if (stats.cog_volume) {
                m.secondaryCog = {label: "COG (volume)", ...(stats.cog_volume as Cog)};
            }
        } else if (stats.cog_volume) {
            m.primaryCog = {label: "COG (volume)", ...(stats.cog_volume as Cog)};
        }
        items.push(m);
    }

    return items;
}

// Per loaded source file. The loader stashes each model's ADA
// extension on its scene group (userData.__adaExt), so multi-model
// overlays keep one extension per source — the old single
// adaExtensionRef only ever held the LAST loaded model. The ref
// remains the fallback for the streaming/replace path, which doesn't
// register per-source groups. Subscribing to loadedSourceNames keeps
// the list live across load/unload (no more snapshot-on-mount).
const StatsSection = () => {
    const {adaExtension: adaExtensionRef} = useViewerRefs();
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);

    const sources: SourceStats[] = [];
    for (const name of loadedSourceNames) {
        const group = loadedSourceGroups.get(name);
        if (!group) continue;
        const ext = (group.children?.[0] as any)?.userData?.__adaExt
            ?? (group as any)?.userData?.__adaExt;
        if (!ext) continue;
        const models = statsFromExtension(ext);
        if (models.length > 0) sources.push({source: name, models});
    }
    if (sources.length === 0 && adaExtensionRef.current) {
        const models = statsFromExtension(adaExtensionRef.current);
        if (models.length > 0) {
            sources.push({
                source: loadedSourceNames.size === 1
                    ? Array.from(loadedSourceNames)[0]
                    : "",
                models,
            });
        }
    }

    if (sources.length === 0) {
        return (
            <div className="text-xs italic opacity-70">
                No stats baked into the loaded model(s).
            </div>
        );
    }

    const showSourceHeaders = sources.length > 1;

    return (
        <div className="space-y-3 text-xs">
            {sources.map((s) => (
                <div key={s.source || "::active"} className="space-y-2">
                    {showSourceHeaders && (
                        <div
                            className="text-[10px] uppercase tracking-wide opacity-60 truncate"
                            title={s.source}
                        >
                            {s.source.split("/").pop() ?? s.source}
                        </div>
                    )}
                    {s.models.map((m) => (
                        <ModelStatsRow key={`${s.source}::${m.key}`} model={m}/>
                    ))}
                </div>
            ))}
        </div>
    );
};

const fmt = (n: number) => (Math.abs(n) >= 1e4 || (n !== 0 && Math.abs(n) < 1e-3) ? n.toExponential(3) : n.toFixed(3));

// One labeled key/value line — muted label left, mono value right.
const StatRow: React.FC<{label: string; value: React.ReactNode; muted?: boolean}> = ({label, value, muted}) => (
    <div className={"flex items-baseline justify-between gap-2 " + (muted ? "opacity-70" : "")}>
        <span className="opacity-70 shrink-0">{label}</span>
        <span className="font-mono tabular-nums text-right truncate">{value}</span>
    </div>
);

const CogRows: React.FC<{cog: {label: string} & Cog; muted?: boolean}> = ({cog, muted}) => (
    <>
        <StatRow
            label={cog.label}
            value={`${fmt(cog.x)}, ${fmt(cog.y)}, ${fmt(cog.z)}`}
            muted={muted}
        />
        {cog.total_mass != null && (
            <StatRow label="Total mass" value={fmt(cog.total_mass)} muted={muted}/>
        )}
        {cog.total_volume != null && (
            <StatRow label="Total volume" value={fmt(cog.total_volume)} muted={muted}/>
        )}
    </>
);

const ModelStatsRow: React.FC<{ model: ModelStats }> = ({model}) => {
    const countsList = Object.entries(model.counts).filter(([, v]) => v > 0);
    return (
        <div className="rounded-sm border border-gray-700/60 bg-gray-800/40 p-1.5 space-y-1">
            <div className="flex items-center gap-1.5 min-w-0">
                <span className="font-semibold truncate" title={model.name}>{model.name}</span>
                <span
                    className={
                        "shrink-0 px-1 rounded-sm text-[9px] uppercase tracking-wide text-white " +
                        (model.kind === "FEA" ? "bg-violet-700" : "bg-sky-700")
                    }
                >
                    {model.kind}
                </span>
            </div>
            {(model.primaryCog || model.secondaryCog) && (
                <div className="space-y-0.5">
                    {model.primaryCog && <CogRows cog={model.primaryCog}/>}
                    {model.secondaryCog && <CogRows cog={model.secondaryCog} muted/>}
                </div>
            )}
            {countsList.length > 0 && (
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 pt-0.5 border-t border-gray-700/50">
                    {countsList.map(([k, v]) => (
                        <div key={k} className="flex items-baseline justify-between gap-2 min-w-0">
                            <span className="opacity-70 truncate" title={k}>{k}</span>
                            <span className="font-mono tabular-nums">{v}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default StatsSection;
