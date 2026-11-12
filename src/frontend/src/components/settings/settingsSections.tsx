// The viewer's settings, one entry per section: what the Settings window draws as tabs and what the
// Options panel draws as collapsible sections when a shell picks them (`OptionsBody`'s `sections`).
// One list, so the two can never disagree about what a section holds.

import React from "react";

import {runtime} from "@/runtime/config";
import AppearanceOptions from "@/components/options/AppearanceOptions";
import CameraControlsOptions from "@/components/options/CameraControlsOptions";
import ComputeOptions from "@/components/options/ComputeOptions";
import DisplayOptions from "@/components/options/DisplayOptions";
import ExperimentalOptions from "@/components/options/ExperimentalOptions";
import LayoutOptions from "@/components/options/LayoutOptions";
import PerformanceOptions from "@/components/options/PerformanceOptions";
import PointSizeOptions from "@/components/options/PointSizeOptions";
import SceneRestoreOptions from "@/components/options/SceneRestoreOptions";
import SpaceMouseOptions from "@/components/options/SpaceMouseOptions";
import ThemeOptions from "@/components/options/ThemeOptions";

/** The settings sections, by id. The same ids as `OptionsSectionId`'s settings sections. */
export type SettingsSectionId =
    | "scene"
    | "appearance"
    | "session"
    | "theme"
    | "performance"
    | "compute"
    | "camera"
    | "spacemouse"
    | "conversion";

export interface SettingsSection {
    id: SettingsSectionId;
    title: string;
    /** False where the section does not apply (REST-only settings in the desktop/websocket viewer). */
    available: () => boolean;
    render: () => React.ReactNode;
}

const always = () => true;
const restOnly = () => runtime.isRestMode();

/** Every settings section, in the classic order. */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
    {
        id: "scene",
        title: "Scene config",
        available: always,
        render: () => (
            <div className="space-y-4">
                <PointSizeOptions/>
                <DisplayOptions/>
            </div>
        ),
    },
    // Lights, colours and the background, the same on every renderer.
    {id: "appearance", title: "Appearance", available: always, render: () => <AppearanceOptions/>},
    {id: "session", title: "Session restore", available: restOnly, render: () => <SceneRestoreOptions/>},
    {
        id: "theme",
        title: "Theme",
        available: always,
        render: () => (
            <>
                <ThemeOptions/>
                {/* The UI shell switcher is the Options panel's own ("interface"), not a setting. */}
                <LayoutOptions className="mt-3"/>
            </>
        ),
    },
    {id: "performance", title: "Performance", available: always, render: () => <PerformanceOptions/>},
    {id: "compute", title: "Calculations", available: always, render: () => <ComputeOptions/>},
    {id: "camera", title: "Camera controls", available: always, render: () => <CameraControlsOptions/>},
    {id: "spacemouse", title: "3D mouse", available: always, render: () => <SpaceMouseOptions/>},
    // The in-browser (WASM) conversion engine only makes sense for the hosted REST viewer.
    {id: "conversion", title: "Conversion engine", available: restOnly, render: () => <ExperimentalOptions/>},
];

/** The sections that apply in this viewer. */
export function availableSettingsSections(): SettingsSection[] {
    return SETTINGS_SECTIONS.filter((s) => s.available());
}
