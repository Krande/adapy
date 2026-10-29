import React, {useEffect} from "react";

import {useViewerStores} from "@/state/AdaViewerContext";
import {bindOverlays, refreshOverlays} from "@/utils/overlays/overlayController";
import {markCount, OVERLAYS} from "@/utils/overlays/overlayModel";

const LEGEND_MAX = 12;

/** Scene > Overlays: core's overlays over what is loaded, each a toggle. What a provider publishes
 *  into the relations index is what lights one up; a model with none says so. */
const OverlaysPanel = () => {
    const {useOverlayStore} = useViewerStores();
    const enabled = useOverlayStore((s) => s.enabled);
    const setEnabled = useOverlayStore((s) => s.setEnabled);
    const status = useOverlayStore((s) => s.status);
    const error = useOverlayStore((s) => s.error);
    const marks = useOverlayStore((s) => s.marks);
    const collections = useOverlayStore((s) => s.collections);
    const notIndexed = useOverlayStore((s) => s.notIndexed);

    // Bound once and left bound: the overlays stay drawn when another tab is shown.
    useEffect(() => {
        bindOverlays();
    }, []);

    const read = status === "ready" || status === "loading";
    return (
        <div className="flex flex-col gap-2">
            <p className="text-xs text-gray-400">
                Drawn from the relations providers publish for the models loaded from published sources.
            </p>
            {OVERLAYS.map((o) => (
                <div key={o.id} className="flex flex-col gap-1">
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={enabled[o.id]}
                            onChange={(e) => setEnabled(o.id, e.target.checked)}
                        />
                        <span className="font-semibold">{o.label}</span>
                        {read && <span className="text-xs text-gray-400">{markCount(marks, o.id)}</span>}
                    </label>
                    <p className="text-xs text-gray-400 pl-6">{o.description}</p>
                    {o.id === "systems" && enabled.systems && marks.systems.entries.length > 0 && (
                        <ul className="pl-6 text-xs flex flex-col gap-0.5">
                            {marks.systems.entries.slice(0, LEGEND_MAX).map((s) => (
                                <li key={s.name} className="flex items-center gap-1.5">
                                    <span
                                        className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                                        style={{background: s.colour}}
                                        aria-hidden="true"
                                    />
                                    <span className="truncate" title={s.name}>
                                        {s.name}
                                    </span>
                                    <span className="text-gray-400 ml-auto">
                                        {s.kind ? `${s.kind} · ` : ""}
                                        {s.members.length}
                                    </span>
                                </li>
                            ))}
                            {marks.systems.entries.length > LEGEND_MAX && (
                                <li className="text-gray-400">
                                    and {marks.systems.entries.length - LEGEND_MAX} more
                                </li>
                            )}
                        </ul>
                    )}
                    {o.id === "flow" && enabled.flow && marks.flow.arrows.length > 0 && (
                        <p className="pl-6 text-xs text-gray-400">
                            {marks.flow.flowKnown > 0 && <>{marks.flow.flowKnown} in the medium's direction (cyan). </>}
                            {marks.flow.modelledOnly > 0 && (
                                <>{marks.flow.modelledOnly} in modelled order only, not a confirmed flow direction (amber).</>
                            )}
                        </p>
                    )}
                </div>
            ))}
            <div className="flex items-center gap-2 text-xs text-gray-400 pt-1 border-t border-white/10">
                <span className="flex-1">
                    {status === "loading" && "Reading relations…"}
                    {status === "error" && <span className="text-red-400">{error}</span>}
                    {status === "ready" &&
                        (collections.length
                            ? `Read ${collections.length} collection${collections.length === 1 ? "" : "s"}.`
                            : "No model loaded from a published source.")}
                    {status === "ready" && notIndexed.length > 0 && ` No relations published for ${notIndexed.join(", ")}.`}
                </span>
                <button
                    type="button"
                    className="px-2 py-0.5 rounded border border-white/20 hover:bg-white/10"
                    onClick={() => void refreshOverlays()}
                >
                    Refresh
                </button>
            </div>
        </div>
    );
};

export default OverlaysPanel;
