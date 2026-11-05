import {PanelCard} from "@/components/ui/PanelFrame";
import {CollapsibleSection} from "@/components/ui/CollapsibleSection";
import React, {Suspense} from "react";
import {useIsNarrow} from "@/utils/useIsMobile";
import {runtime} from "@/runtime/config";
import {useOptionsStore} from "@/state/optionsStore";
import PointSizeOptions from "./options/PointSizeOptions";
import DisplayOptions from "./options/DisplayOptions";
import LayoutOptions from "./options/LayoutOptions";
import ExperimentalOptions from "./options/ExperimentalOptions";
import PerformanceOptions from "./options/PerformanceOptions";
import SceneRestoreOptions from "./options/SceneRestoreOptions";
import ShortcutsModal from "./options/ShortcutsModal";
import SpaceMouseOptions from "./options/SpaceMouseOptions";
import ThemeOptions from "./options/ThemeOptions";

// REST-only controls (scope picker, signed-in row, admin button) live
// here so they're reachable on phones and don't crowd the top bar.
// Lazy-loaded so the desktop bundle stays slim.
const RestSection = React.lazy(() => import("./options/RestSection"));
import {UiShellSwitcher} from "@/plugins";
import { buildLabel } from "@/utils/buildLabel";

// Each section starts closed; click the header to toggle.
const Section: React.FC<{title: string; children: React.ReactNode}> = ({title, children}) => (
    <CollapsibleSection title={title} variant="caret" defaultOpen={false}>
        {children}
    </CollapsibleSection>
);

// Two layouts:
// * Mobile: full-height slide-in drawer from the left edge. There
//   isn't enough room on a phone for an inline box; the drawer takes
//   over so the user can scroll through the options.
// * Desktop: an inline panel in the menu's info-box column, styled
//   like ObjectInfoBox / SceneInfoBox / StorageBrowser — same
//   semi-transparent gray, same rounded corners. The ☰ button toggles
//   it; nothing else needs to.
/** The Options panel's content: the build line and every settings section. */
export function OptionsBody() {
    const unique_version_id = runtime.uniqueVersionId();
    const adapy_version = runtime.adapyVersion();
    const frontend_sha = runtime.frontendSha();
    const viewer_image_tag = runtime.viewerImageTag();
    const adapy_build_ref = runtime.adapyBuildRef();

    const build_label = buildLabel(
        adapy_version,
        frontend_sha,
        viewer_image_tag,
        unique_version_id,
        adapy_build_ref,
    );
    const versionInfo = (
        <div className="text-xs text-gray-300">
            Build: <span className="font-mono">{build_label}</span>
        </div>
    );

    const sections = (
        <>
            {runtime.isRestMode() && (
                <>
                    <Suspense fallback={null}>
                        <RestSection/>
                    </Suspense>
                    <hr className="border-gray-600"/>
                </>
            )}
            {/* Same idea for the scene-config knobs. The defaults are
                tuned for the common case; tucking the controls behind a
                disclosure keeps the drawer from feeling overwhelming. */}
            <Section title="Scene config">
                <div className="space-y-4">
                    <PointSizeOptions/>
                    <DisplayOptions/>
                </div>
            </Section>
            <hr className="border-gray-600"/>
            {runtime.isRestMode() && (
                <>
                    <Section title="Session restore">
                        <SceneRestoreOptions/>
                    </Section>
                    <hr className="border-gray-600"/>
                </>
            )}
            <Section title="Theme">
                <ThemeOptions/>
                {/* Renders nothing unless a build actually carries more than one
                    shell, so this costs a stock viewer no space at all. Grouped
                    with Theme because both answer "how does this app look",
                    and neither is a decision anyone revisits often. */}
                <UiShellSwitcher className="mt-3"/>
                <LayoutOptions className="mt-3"/>
            </Section>
            <hr className="border-gray-600"/>
            <Section title="Performance">
                <PerformanceOptions/>
            </Section>
            <hr className="border-gray-600"/>
            <Section title="3D mouse">
                <SpaceMouseOptions/>
            </Section>
            <hr className="border-gray-600"/>
            {/* The in-browser (WASM) conversion engine only makes sense for the
                hosted REST viewer; the websocket/desktop and embed bundles
                don't expose it. */}
            {runtime.isRestMode() && (
                <>
                    <Section title="Conversion engine">
                        <ExperimentalOptions/>
                    </Section>
                    <hr className="border-gray-600"/>
                </>
            )}
            <ShortcutsModal/>
        </>
    );

    return (
        <>
            {versionInfo}
            {sections}
        </>
    );
}

// The classic frame around `OptionsBody`, in two layouts (see above).
function OptionsComponent() {
    const isMobile = useIsNarrow();
    const setIsOptionsVisible = useOptionsStore((s) => s.setIsOptionsVisible);

    if (isMobile) {
        // Drawer pinned to the left edge, full visible-viewport height.
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-gray-800 text-white text-sm shadow-xl flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-gray-700">
                    <span className="font-bold text-base">Options</span>
                    <button
                        type="button"
                        onClick={() => setIsOptionsVisible(false)}
                        className="text-gray-300 hover:text-white text-xl leading-none px-2"
                        aria-label="Close options"
                        title="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4 flex flex-col space-y-4">
                    <OptionsBody/>
                </div>
            </div>
        );
    }

    // Desktop: matches the other info boxes (Info / Group / Storage)
    // visually so the user reads the menu-bar column as one consistent
    // pattern. Cap max-height so a long Options list doesn't push the
    // page; scroll inside instead.
    return (
        <PanelCard className="min-w-80 max-w-sm text-sm space-y-3 max-h-[70vh] overflow-y-auto" heading="Options">
            <OptionsBody/>
        </PanelCard>
    );
}

export default OptionsComponent;
