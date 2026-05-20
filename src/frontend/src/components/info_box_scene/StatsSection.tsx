import React, {useEffect, useState} from "react";
import {useViewerRefs} from "@/state/AdaViewerContext";

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

const StatsSection = () => {
    const {adaExtension: adaExtensionRef} = useViewerRefs();
    const [models, setModels] = useState<ModelStats[]>([]);

    // Snapshot on mount. Same trade-off as GroupsSection: the panel
    // re-mounts when the user toggles it open, so opening after a model
    // load picks up the fresh extension. Reloads while the panel is
    // open won't refresh — fix that the day someone hits it.
    useEffect(() => {
        const ext = adaExtensionRef.current;
        if (!ext) {
            setModels([]);
            return;
        }

        const items: ModelStats[] = [];

        for (const sim of ext.simulation_objects ?? []) {
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

        for (const d of ext.design_objects ?? []) {
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

        setModels(items);
    }, [adaExtensionRef]);

    if (models.length === 0) {
        return (
            <div className="text-xs italic opacity-70">
                No stats baked into the loaded model(s).
            </div>
        );
    }

    return (
        <div className="space-y-2 text-xs">
            {models.map((m) => (
                <ModelStatsRow key={m.key} model={m}/>
            ))}
        </div>
    );
};

const fmt = (n: number) => (Math.abs(n) >= 1e4 || (n !== 0 && Math.abs(n) < 1e-3) ? n.toExponential(3) : n.toFixed(3));

const formatCog = (c: Cog) => `(${fmt(c.x)}, ${fmt(c.y)}, ${fmt(c.z)})`;

const ModelStatsRow: React.FC<{ model: ModelStats }> = ({model}) => {
    const countsList = Object.entries(model.counts).filter(([, v]) => v > 0);
    return (
        <div>
            <div className="font-semibold">
                {model.name}{" "}
                <span className="font-normal opacity-70">({model.kind})</span>
            </div>
            {model.primaryCog && (
                <div>
                    {model.primaryCog.label}: {formatCog(model.primaryCog)}
                    {model.primaryCog.total_mass != null && (
                        <> · mass {fmt(model.primaryCog.total_mass)}</>
                    )}
                    {model.primaryCog.total_volume != null && (
                        <> · vol {fmt(model.primaryCog.total_volume)}</>
                    )}
                </div>
            )}
            {model.secondaryCog && (
                <div className="opacity-70">
                    {model.secondaryCog.label}: {formatCog(model.secondaryCog)}
                    {model.secondaryCog.total_volume != null && (
                        <> · vol {fmt(model.secondaryCog.total_volume)}</>
                    )}
                </div>
            )}
            {countsList.length > 0 && (
                <div>
                    {countsList.map(([k, v]) => `${k}: ${v}`).join(" · ")}
                </div>
            )}
        </div>
    );
};

export default StatsSection;
