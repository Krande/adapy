// "Request from source" in a selection box's right-click menu: which elements (partially or fully
// within the box), which source to ask (the first available one by default, any other by choice),
// then the question -- "N members have no geometry there,
// request them?" -- and the answer. The flow lives in `state/boxRequestStore`, so it goes on (and
// the Scene panel's box list shows it) after the menu closes.

import React, {useEffect} from "react";

import type {SelectionBox} from "@/state/bboxSelectionStore";
import {
    confirmBoxRequest,
    isBoxRequestBusy,
    loadBoxMembers,
    planBoxRequest,
    prepareBoxRequest,
    requestBoxBounds,
    useBoxRequestStore,
    type BoundsOffer,
    type BoxRequestEntry,
} from "@/state/boxRequestStore";
import type {BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import type {BoxCandidates} from "@/utils/bboxSelection/boxRequest";

const MODES: {mode: BoxSelectMode; label: string; title: string}[] = [
    {mode: "partial", label: "Partially within", title: "Request every element the box touches"},
    {mode: "full", label: "Fully within", title: "Request only elements entirely inside the box"},
];

const btn = "rounded-sm px-1.5 py-0.5 text-[11px] text-gray-100 disabled:opacity-40";

export const BoxRequestSection: React.FC<{box: SelectionBox}> = ({box}) => {
    const entry = useBoxRequestStore((s) => s.byBox[box.id]);
    const chooseProvider = useBoxRequestStore((s) => s.chooseProvider);
    const busy = isBoxRequestBusy(entry);

    // On open: read the choices for the box as it is now, in the request's mode -- the box's own
    // selection mode the first time.
    useEffect(() => {
        void prepareBoxRequest(box.id, entry?.mode ?? box.mode);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [box.id, box.min, box.max]);

    const mode = entry?.mode ?? box.mode;
    const choices = entry?.choices ?? [];
    const canAsk = !!entry && entry.phase !== "loading" && !busy && !entry.blocked && !!entry.providerId;

    return (
        <div className="px-3 py-1.5 text-xs" role="group" aria-label="Request from source">
            <div className="mb-1 flex items-center gap-2">
                <span className="font-medium">Request from source</span>
                {entry?.phase === "loading" && <span className="text-[10px] text-gray-400">reading…</span>}
            </div>
            <div className="flex gap-1">
                {MODES.map((m) => (
                    <label key={m.mode} className="flex items-center gap-1 text-[11px]" title={m.title}>
                        <input
                            type="radio"
                            name={`box-request-mode-${box.id}`}
                            checked={mode === m.mode}
                            disabled={busy}
                            onChange={() => void prepareBoxRequest(box.id, m.mode)}
                        />
                        {m.label}
                    </label>
                ))}
            </div>
            {choices.length > 1 && (
                <label className="mt-1 block text-[10px] text-gray-300">
                    From
                    <select
                        className="mt-0.5 block w-full rounded-sm bg-gray-700 text-[11px] text-white"
                        value={entry?.providerId ?? ""}
                        disabled={busy}
                        onChange={(e) => chooseProvider(box.id, e.target.value)}
                    >
                        {choices.map((c) => (
                            <option key={c.providerId} value={c.providerId}>
                                {c.label}
                            </option>
                        ))}
                    </select>
                </label>
            )}
            {choices.length === 1 && <div className="mt-1 text-[10px] text-gray-300">From {choices[0].label}</div>}
            {entry?.candidates && entry.phase === "ready" && <CandidatesNote candidates={entry.candidates}/>}
            {entry?.boundsOffer && entry.candidates?.kind === "drawn" && !busy && (
                <BoundsOfferNote boxId={box.id} offer={entry.boundsOffer}/>
            )}
            {entry?.blocked && <div className="mt-1 max-w-64 text-[10px] text-amber-300">{entry.blocked}</div>}
            {entry?.phase !== "confirm" && (
                <div className="mt-1 flex justify-end">
                    <button
                        type="button"
                        role="menuitem"
                        className={`${btn} bg-blue-700 hover:bg-blue-600`}
                        disabled={!canAsk}
                        title="Find the elements in the box the source has no geometry for, then confirm"
                        onClick={() => void planBoxRequest(box.id, box.label)}
                    >
                        Request from source…
                    </button>
                </div>
            )}
            <BoxRequestStatus boxId={box.id} label={box.label} entry={entry}/>
        </div>
    );
};

/** Where the box looks for what it holds: the published bounds (loaded or not), or -- without them,
 *  and why -- only what is drawn. */
const CandidatesNote: React.FC<{candidates: BoxCandidates}> = ({candidates}) =>
    candidates.kind === "bounds" ? (
        <div className="mt-1 max-w-64 text-[10px] text-gray-400">
            Looks in {candidates.providers.join(", ")}'s published bounds: loaded or not.
        </div>
    ) : (
        <div className="mt-1 max-w-64 text-[10px] text-amber-300">
            Only what is drawn{candidates.why ? `: ${candidates.why}` : ""}.
        </div>
    );

/** No bounds published for the scene's collections, and a provider that can measure them: the
 *  offer to, the run once asked, or why this user cannot ask. */
const BoundsOfferNote: React.FC<{boxId: string; offer: BoundsOffer}> = ({boxId, offer}) => {
    const what = offer.collections.join(", ");
    if (offer.runId) {
        return (
            <div className="mt-1 max-w-64 text-[10px] text-gray-300" role="status">
                {offer.provider} is measuring the node bounds of {what}; this box reads them when it finishes (progress in the job toast).
            </div>
        );
    }
    if (!offer.allowed) {
        return (
            <div className="mt-1 max-w-64 text-[10px] text-gray-400">
                {offer.provider} can measure the node bounds of {what}, but {offer.reason ?? "you may not ask it"}. Ask an administrator to run it.
            </div>
        );
    }
    return (
        <div className="mt-1 max-w-64 text-[10px] text-gray-300">
            <div>
                Extract the node bounds of {what} from {offer.provider} (every level of the project) so the box finds what is in
                it, loaded or not. One job per project.
            </div>
            {offer.error && <div className="mt-0.5 text-red-400">{offer.error}</div>}
            <div className="mt-1 flex justify-end">
                <button
                    type="button"
                    role="menuitem"
                    className={`${btn} bg-emerald-700 hover:bg-emerald-600`}
                    title={`Run ${offer.provider}'s node bounds extraction for ${what}, then read the box again`}
                    onClick={() => void requestBoxBounds(boxId)}
                >
                    Extract node bounds
                </button>
            </div>
        </div>
    );
};

/** Where a box's request has got to, with the confirm step: the menu's and the Scene panel's. */
export const BoxRequestStatus: React.FC<{boxId: string; label?: string; entry?: BoxRequestEntry}> = ({boxId, label, entry: given}) => {
    const stored = useBoxRequestStore((s) => s.byBox[boxId]);
    const patch = useBoxRequestStore((s) => s.patch);
    const entry = given ?? stored;
    if (!entry || !entry.message || entry.phase === "loading" || entry.phase === "ready") return null;
    const tone = entry.phase === "error" ? "text-red-400" : entry.phase === "confirm" ? "text-amber-100" : "text-gray-300";
    const loadable = entry.loadable.length;
    return (
        <div className={`mt-1 max-w-64 text-[10px] ${tone}`} role={entry.phase === "confirm" ? "alertdialog" : "status"}>
            <div className="break-words">
                {entry.message}
                {isBoxRequestBusy(entry) && "…"}
            </div>
            {loadable > 0 && !isBoxRequestBusy(entry) && (
                <div className="mt-1 flex justify-end">
                    <button
                        type="button"
                        className={`${btn} bg-emerald-700 hover:bg-emerald-600`}
                        title="Load what the source already has, and the scene does not, into the scene"
                        onClick={() => void loadBoxMembers(boxId, label ?? "Box")}
                    >
                        Load {loadable} into scene
                    </button>
                </div>
            )}
            {entry.phase === "confirm" && (
                <div className="mt-1 flex justify-end gap-1">
                    <button type="button" autoFocus className={`${btn} bg-blue-700 hover:bg-blue-600`} onClick={() => void confirmBoxRequest(boxId)}>
                        Request
                    </button>
                    <button
                        type="button"
                        className={`${btn} bg-gray-700 hover:bg-gray-600`}
                        onClick={() => patch(boxId, {phase: "ready", plan: null, message: null})}
                    >
                        Cancel
                    </button>
                </div>
            )}
            {(entry.phase === "done" || entry.phase === "error") && (
                <div className="mt-0.5 flex justify-end">
                    <button
                        type="button"
                        className="text-[10px] text-gray-400 hover:text-gray-200"
                        onClick={() => patch(boxId, {phase: "ready", plan: null, message: null})}
                    >
                        Dismiss
                    </button>
                </div>
            )}
        </div>
    );
};

export default BoxRequestSection;
