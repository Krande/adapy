import React, {useEffect, useState} from "react";
import {ApiError, viewerApi} from "@/services/viewerApi";

// Per-deployment knobs that affect future runs. Profile toggle persists in
// app_settings; Clear metrics nulls out columns + deletes blobs. Visually
// separated so the controls are obvious on mobile (where they otherwise sit
// flush with the Filters / Refresh row).

const PROFILE_SETTING_KEY = "profile_conversions";

const MetricsControls: React.FC<{
    onError: (msg: string | null) => void;
    /** Reload the table after metrics were cleared. */
    onCleared: () => Promise<void>;
}> = ({onError, onCleared}) => {
    const [profileEnabled, setProfileEnabled] = useState(false);
    const [profileSaving, setProfileSaving] = useState(false);
    const [clearing, setClearing] = useState(false);

    // Initial fetch of the profile-conversions toggle. Failures are
    // non-fatal — the row still renders, the toggle just stays off.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const v = await viewerApi.adminGetSetting(PROFILE_SETTING_KEY);
                if (!cancelled) setProfileEnabled((v || "").toLowerCase() === "true");
            } catch {
                /* ignore */
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const onProfileToggle = async (next: boolean) => {
        setProfileSaving(true);
        try {
            await viewerApi.adminSetSetting(PROFILE_SETTING_KEY, next ? "true" : "false");
            setProfileEnabled(next);
        } catch (e) {
            onError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setProfileSaving(false);
        }
    };

    const onClearMetrics = async () => {
        if (!window.confirm(
            "Clear all conversion metrics and delete profile blobs? Audit rows themselves stay; only the metrics columns are nulled."
        )) return;
        setClearing(true);
        try {
            const r = await viewerApi.adminClearMetrics();
            await onCleared();
            window.alert(
                `Cleared ${r.rows_cleared} row(s); deleted ${r.profiles_deleted} profile blob(s).` +
                (r.errors.length ? `\n${r.errors.length} error(s) — see browser console.` : "")
            );
            if (r.errors.length) console.warn("clear metrics errors", r.errors);
        } catch (e) {
            onError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setClearing(false);
        }
    };

    return (
        <div className="border-b border-gray-700">
            <div className="flex flex-wrap items-center gap-3 px-3 sm:px-4 py-2 text-xs border-t border-gray-800 bg-gray-900/40">
                <span className="font-semibold text-gray-300 uppercase tracking-wide text-[10px]">
                    Metrics
                </span>
                <label className="flex items-center gap-2 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={profileEnabled}
                        onChange={(e) => onProfileToggle(e.target.checked)}
                        disabled={profileSaving}
                        className="h-4 w-4"
                    />
                    <span>
                        Profile conversions
                        {profileSaving ? <span className="text-gray-400"> (saving…)</span> : null}
                    </span>
                </label>
                <button
                    className="ml-auto bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm disabled:opacity-50"
                    onClick={onClearMetrics}
                    disabled={clearing}
                    title="Null out all metrics columns and delete profile blobs"
                >
                    {clearing ? "Clearing…" : "Clear metrics"}
                </button>
            </div>
        </div>
    );
};

export default MetricsControls;
