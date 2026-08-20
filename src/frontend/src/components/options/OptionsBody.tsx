import React, {Suspense} from "react";
import {CollapsibleSection} from "@/components/ui";
import {runtime} from "@/runtime/config";
import PointSizeOptions from "./PointSizeOptions";
import DisplayOptions from "./DisplayOptions";
import ExperimentalOptions from "./ExperimentalOptions";
import PerformanceOptions from "./PerformanceOptions";
import ThemeOptions from "./ThemeOptions";

// The preferences content, with no chrome of its own.
//
// Extracted from OptionsComponent so the shell's dock can host it directly. Previously
// the shell mounted OptionsComponent, which drew its own bordered, scrolling panel —
// producing a box inside the dock's box, complete with a second scrollbar.
//
// OptionsComponent still exists as the classic UI's mobile drawer / desktop panel and
// renders this; both go at cutover.

// REST-only controls (scope picker, signed-in row, admin button). Lazy so the desktop
// bundle stays slim.
const RestSection = React.lazy(() => import("./RestSection"));

/** Build identity — adapy version plus the commit sha, when either is known. */
function BuildLine() {
    const unique_version_id = runtime.uniqueVersionId();
    const adapy_version = runtime.adapyVersion();
    const frontend_sha = runtime.frontendSha();
    const viewer_image_tag = runtime.viewerImageTag();

    // The sha is the frontend build-time git sha when present, else the deployed image's
    // sha tag (VIEWER_IMAGE_TAG = "sha-XXXXXXX" on branch builds — the hosted viewer
    // copies source, so the build-time git sha is unavailable there). Falls back to the
    // numeric build id as a last resort.
    const sha = frontend_sha || (viewer_image_tag.startsWith("sha-") ? viewer_image_tag.slice(4) : "");
    const build_label = adapy_version
        ? sha
            ? `${adapy_version} (${sha})`
            : adapy_version
        : sha || String(unique_version_id);

    return (
        <div className="text-xs text-content-subtle">
            Build <span className="font-mono">{build_label}</span>
        </div>
    );
}

export default function OptionsBody() {
    return (
        <div className="flex flex-col gap-3 text-sm">
            <BuildLine />

            {runtime.isRestMode() && (
                <Suspense fallback={null}>
                    <RestSection />
                </Suspense>
            )}

            {/* Defaults are tuned for the common case, so the knobs sit behind disclosures
                rather than presenting thirty controls at once. */}
            <CollapsibleSection title="Scene config" defaultOpen={false}>
                <div className="flex flex-col gap-4 pt-1">
                    <PointSizeOptions />
                    <DisplayOptions />
                </div>
            </CollapsibleSection>

            <CollapsibleSection title="Theme" defaultOpen={false}>
                <div className="pt-1">
                    <ThemeOptions />
                </div>
            </CollapsibleSection>

            <CollapsibleSection title="Performance" defaultOpen={false}>
                <div className="pt-1">
                    <PerformanceOptions />
                </div>
            </CollapsibleSection>

            {/* The in-browser (WASM) conversion engine only makes sense for the hosted
                REST viewer; the websocket/desktop and embed bundles don't expose it. */}
            {runtime.isRestMode() && (
                <CollapsibleSection title="Conversion engine" defaultOpen={false}>
                    <div className="pt-1">
                        <ExperimentalOptions />
                    </div>
                </CollapsibleSection>
            )}
        </div>
    );
}
