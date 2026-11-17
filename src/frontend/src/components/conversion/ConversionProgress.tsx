// The progress toast stack: conversions and operations, the scene load, storage sweeps,
// failed jobs, the admin audit badge, and the toasts shells and plugins add.

import React, { useSyncExternalStore } from "react";
import { listToastSlots, subscribeToastSlots, toastSlotsVersion } from "@/layout/toastSlots";
import { useConversionStore } from "@/state/conversionStore";
import { useCompressionStore } from "@/state/compressionStore";
import { useMeStore } from "@/state/meStore";
import { useLoadQueueStore } from "@/state/loadQueueStore";
import { useGalleryStore } from "@/state/galleryStore";
import { eitherClaimed, withoutClaimed, type IsClaimed } from "./claimedLoads";
import { isLoadClaimed, loadClaimsVersion, subscribeLoadClaims } from "@/plugins/loadClaims";
import { AuditActivityBadge } from "./conversionProgress/AuditActivityBadge";
import { CompressionToast } from "./conversionProgress/CompressionToast";
import { UnifiedToast } from "./conversionProgress/UnifiedToast";
import { ErrorStack } from "./conversionProgress/errors";
import { toastRows } from "./conversionProgress/toastRows";

export interface ConversionProgressProps {
    /** Loads whose progress the host tells elsewhere, left out of the toast, in addition to
     *  the ones claimed with `claimLoad` (core's storage rows claim theirs). */
    isClaimed?: IsClaimed;
    /** The corner of its host the toast sits in. Default `"bottom-right"`. */
    anchor?: ToastAnchor;
}

export type ToastAnchor = "bottom-right" | "bottom-left" | "top-right" | "top-left";

// Phones: across the bottom (or top), 16px from each edge, so a long source key
// cannot overflow; from `sm:` up, a pill in its corner capped at `max-w-sm`.
const TOAST_ANCHOR_CLASS: Readonly<Record<ToastAnchor, string>> = {
    "bottom-right": "absolute bottom-4 left-4 right-4 sm:left-auto sm:max-w-sm z-50 flex flex-col gap-2 pointer-events-auto",
    "bottom-left": "absolute bottom-4 left-4 right-4 sm:right-auto sm:max-w-sm z-50 flex flex-col gap-2 pointer-events-auto",
    "top-right": "absolute top-4 left-4 right-4 sm:left-auto sm:max-w-sm z-50 flex flex-col gap-2 pointer-events-auto",
    "top-left": "absolute top-4 left-4 right-4 sm:right-auto sm:max-w-sm z-50 flex flex-col gap-2 pointer-events-auto",
};

const ConversionProgress: React.FC<ConversionProgressProps> = ({isClaimed: hostClaimed, anchor = "bottom-right"}) => {
    // Shells' and plugins' toast slots: re-render when one is added or removed.
    useSyncExternalStore(subscribeToastSlots, toastSlotsVersion, toastSlotsVersion);
    useSyncExternalStore(subscribeLoadClaims, loadClaimsVersion, loadClaimsVersion);
    const isClaimed = eitherClaimed(isLoadClaimed, hostClaimed);
    const jobs = useConversionStore((s) => s.jobs);
    const clearJob = useConversionStore((s) => s.clearJob);
    const sweeps = useCompressionStore((s) => s.sweeps);
    const clearSweep = useCompressionStore((s) => s.clearSweep);
    const loadCurrent = useLoadQueueStore((s) => s.current);
    const queued = useLoadQueueStore((s) => s.queued);
    const errors = useLoadQueueStore((s) => s.errors);
    const removeQueued = useLoadQueueStore((s) => s.removeQueued);
    const clearError = useLoadQueueStore((s) => s.clearError);
    const loadCurrentName = loadCurrent?.name ?? null;
    const shownLoad = withoutClaimed({currentName: loadCurrentName, queued, errors}, isClaimed);
    const loadQueued = shownLoad.queued;
    const loadErrors = shownLoad.errors;
    // On mobile the gallery HUD is a bottom bar; lift the toast stack above it so the audit toast
    // stacks on top of the gallery menu instead of overlapping. 0 (desktop/gallery-off) => default.
    const galleryBarHeight = useGalleryStore((s) => s.mobileBarHeight);

    // Which jobs the toast lists, and what the load row shows (`./conversionProgress/toastRows`).
    const {inProgress, errored, operationRunning, loadJob, loadName} = toastRows(jobs, {
        loadCurrentName,
        currentClaimed: shownLoad.currentClaimed,
        isClaimed,
    });
    const loadQueueActive =
        loadName !== null || loadQueued.length > 0 || loadErrors.length > 0;
    const allVisible = [...inProgress, ...errored];
    const visibleSweeps = Object.entries(sweeps);

    // We always render the outer slot so the admin-only
    // AuditActivityBadge has somewhere to mount — the badge polls
    // internally and returns null whenever no audits are running,
    // so an empty container is invisible. The toast + sweeps only
    // render when there's actually something to show.
    const isAdmin = useMeStore.getState().isAdmin;
    const toastSlots = listToastSlots();
    if (allVisible.length === 0 && visibleSweeps.length === 0 && !loadQueueActive && !isAdmin && toastSlots.length === 0) {
        return null;
    }

    return (
        // ``pointer-events-auto`` is explicit so the toast row always
        // receives clicks even if a future ancestor opts into
        // pointer-events-none for the chrome layer (some overlay
        // shells do that to let drags reach the canvas).
        //
        // Mobile width: anchor to both left and right (16px from each
        // edge) so a long source key can't overflow the viewport.
        // From the ``sm:`` breakpoint up we revert to the desktop
        // floating-pill style — anchored to the right, capped at
        // ``max-w-sm`` (24rem).
        <div
            className={TOAST_ANCHOR_CLASS[anchor]}
            style={galleryBarHeight > 0 && anchor.startsWith("bottom") ? {bottom: galleryBarHeight + 16} : undefined}
        >
            <AuditActivityBadge/>
            {/* Shells' and plugins' own toasts (`registerToastSlot`), in the same stack. */}
            {toastSlots.map((slot) => (
                <React.Fragment key={`slot:${slot.id}`}>{slot.render()}</React.Fragment>
            ))}
            {visibleSweeps.map(([scopeLabel, state]) => (
                <CompressionToast
                    key={`compress:${scopeLabel}`}
                    scopeLabel={scopeLabel}
                    state={state}
                    onDismiss={() => clearSweep(scopeLabel)}
                />
            ))}
            <UnifiedToast
                conversionJobs={inProgress}
                onClearJob={clearJob}
                loadName={operationRunning ? null : loadName}
                loadJob={operationRunning ? undefined : loadJob}
                loadQueued={operationRunning ? [] : loadQueued}
                loadErrors={loadErrors}
                onRemoveQueued={removeQueued}
                onClearLoadError={clearError}
            />
            <ErrorStack jobs={errored} onClear={clearJob}/>
        </div>
    );
};

export default ConversionProgress;
