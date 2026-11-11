import {PanelCard} from "@/components/ui/PanelFrame";
import {CollapsibleSection} from "@/components/ui/CollapsibleSection";
import React, {Suspense} from "react";
import {useIsNarrow} from "@/utils/useIsMobile";
import {runtime} from "@/runtime/config";
import {CORE_PANEL_IDS, hide} from "@/layout/panelActions";
import PointSizeOptions from "./options/PointSizeOptions";
import ComputeOptions from "./options/ComputeOptions";
import DisplayOptions from "./options/DisplayOptions";
import LayoutOptions from "./options/LayoutOptions";
import ExperimentalOptions from "./options/ExperimentalOptions";
import PerformanceOptions from "./options/PerformanceOptions";
import SceneRestoreOptions from "./options/SceneRestoreOptions";
import ShortcutsModal from "./options/ShortcutsModal";
import SpaceMouseOptions from "./options/SpaceMouseOptions";
import CameraControlsOptions from "./options/CameraControlsOptions";
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
/** The Options panel's sections, by id. `build` is the build line at the top; `account` is the hosted sign-in / scope / convert / admin block. */
export type OptionsSectionId =
    | "build"
    | "account"
    | "scene"
    | "session"
    | "theme"
    | "performance"
    | "compute"
    | "camera"
    | "spacemouse"
    | "conversion"
    | "shortcuts";

export interface OptionsBodyProps {
    /** The sections, in this order; others are left out. Default: all, in the classic order. */
    sections?: readonly OptionsSectionId[];
    /** Sections left out (after `sections`). */
    hiddenSections?: readonly OptionsSectionId[];
}

const DEFAULT_SECTIONS: readonly OptionsSectionId[] = [
    "build",
    "account",
    "scene",
    "session",
    "theme",
    "performance",
    "compute",
    "camera",
    "spacemouse",
    "conversion",
    "shortcuts",
];

/** The Options panel's content: the build line and every settings section (or those a shell picks). */
export function OptionsBody({sections: picked, hiddenSections}: OptionsBodyProps) {
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

    const rest = runtime.isRestMode();
    // Each section's content; null where it does not apply here. All but the last
    // two (build line, shortcuts button) are followed by a rule.
    const content: Readonly<Record<OptionsSectionId, React.ReactNode>> = {
        build: versionInfo,
        account: rest ? (
            <Suspense fallback={null}>
                <RestSection/>
            </Suspense>
        ) : null,
        // The defaults are tuned for the common case; tucking the controls behind
        // a disclosure keeps the drawer from feeling overwhelming.
        scene: (
            <Section title="Scene config">
                <div className="space-y-4">
                    <PointSizeOptions/>
                    <DisplayOptions/>
                </div>
            </Section>
        ),
        session: rest ? (
            <Section title="Session restore">
                <SceneRestoreOptions/>
            </Section>
        ) : null,
        theme: (
            <Section title="Theme">
                <ThemeOptions/>
                {/* Renders nothing unless a build actually carries more than one
                    shell, so this costs a stock viewer no space at all. Grouped
                    with Theme because both answer "how does this app look",
                    and neither is a decision anyone revisits often. */}
                <UiShellSwitcher className="mt-3"/>
                <LayoutOptions className="mt-3"/>
            </Section>
        ),
        performance: (
            <Section title="Performance">
                <PerformanceOptions/>
            </Section>
        ),
        compute: (
            <Section title="Calculations">
                <ComputeOptions/>
            </Section>
        ),
        camera: (
            <Section title="Camera controls">
                <CameraControlsOptions/>
            </Section>
        ),
        spacemouse: (
            <Section title="3D mouse">
                <SpaceMouseOptions/>
            </Section>
        ),
        // The in-browser (WASM) conversion engine only makes sense for the hosted
        // REST viewer; the websocket/desktop and embed bundles don't expose it.
        conversion: rest ? (
            <Section title="Conversion engine">
                <ExperimentalOptions/>
            </Section>
        ) : null,
        shortcuts: <ShortcutsModal/>,
    };
    const hidden = new Set(hiddenSections ?? []);
    const order = (picked ?? DEFAULT_SECTIONS).filter((id) => !hidden.has(id) && content[id] !== null);
    return (
        <>
            {order.map((id) => (
                <React.Fragment key={id}>
                    {content[id]}
                    {id !== "build" && id !== "shortcuts" && <hr className="border-gray-600"/>}
                </React.Fragment>
            ))}
        </>
    );
}

// The classic frame around `OptionsBody`, in two layouts (see above).
function OptionsComponent() {
    const isMobile = useIsNarrow();

    if (isMobile) {
        // Drawer pinned to the left edge, full visible-viewport height.
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-gray-800 text-white text-sm shadow-xl flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-gray-700">
                    <span className="font-bold text-base">Options</span>
                    <button
                        type="button"
                        onClick={() => void hide(CORE_PANEL_IDS.options)}
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
