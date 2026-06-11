import React from "react";
import {
    effectivePanelTheme,
    THEME_PRESETS,
    ThemePresetId,
    useThemeStore,
} from "@/state/themeStore";

// Theme picker for the menu-row panels. Four preset cards (each a
// live mini-preview of its chrome) plus custom swatches for panel
// background + text and an opacity slider, for landing anywhere
// between "max legibility" and "don't distract from the 3D view".

const ThemeOptions: React.FC = () => {
    const preset = useThemeStore((s) => s.preset);
    const customBg = useThemeStore((s) => s.customBg);
    const customText = useThemeStore((s) => s.customText);
    const bgOpacity = useThemeStore((s) => s.bgOpacity);
    const setPreset = useThemeStore((s) => s.setPreset);
    const setCustomBg = useThemeStore((s) => s.setCustomBg);
    const setCustomText = useThemeStore((s) => s.setCustomText);
    const setBgOpacity = useThemeStore((s) => s.setBgOpacity);
    const resetCustom = useThemeStore((s) => s.resetCustom);

    const hasCustom = customBg !== null || customText !== null;
    const effective = effectivePanelTheme({preset, customBg, customText, bgOpacity});

    return (
        <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 gap-2">
                {(Object.keys(THEME_PRESETS) as ThemePresetId[]).map((id) => {
                    const p = THEME_PRESETS[id];
                    const active = preset === id && !hasCustom;
                    return (
                        <button
                            key={id}
                            type="button"
                            onClick={() => setPreset(id)}
                            title={p.hint}
                            className={
                                "rounded-md border px-2 py-1.5 text-left cursor-pointer " +
                                (active
                                    ? "border-blue-400 ring-1 ring-blue-400"
                                    : "border-gray-600 hover:border-gray-400")
                            }
                            style={{background: p.theme.bg, color: p.theme.text}}
                        >
                            <div className="font-semibold">{p.name}</div>
                            <div className="opacity-80">Aa panel text</div>
                        </button>
                    );
                })}
            </div>
            <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                    <span>Panel color</span>
                    <input
                        type="color"
                        // Color inputs need a hex; when no override is set show
                        // a neutral derived from the active preset family.
                        value={customBg ?? "#111827"}
                        onChange={(e) => setCustomBg(e.target.value)}
                        className="h-6 w-10 cursor-pointer rounded-sm border border-gray-600 bg-transparent"
                        title="Custom panel background"
                    />
                </div>
                <div className="flex items-center justify-between gap-2">
                    <span>Text color</span>
                    <input
                        type="color"
                        value={customText ?? "#f3f4f6"}
                        onChange={(e) => setCustomText(e.target.value)}
                        className="h-6 w-10 cursor-pointer rounded-sm border border-gray-600 bg-transparent"
                        title="Custom panel text color"
                    />
                </div>
                <div className="flex items-center justify-between gap-2">
                    <span className={customBg ? "" : "opacity-50"}>
                        Panel opacity
                    </span>
                    <input
                        type="range"
                        min={0.1}
                        max={1}
                        step={0.05}
                        value={bgOpacity}
                        onChange={(e) => setBgOpacity(Number(e.target.value))}
                        disabled={!customBg}
                        className="w-28 cursor-pointer disabled:cursor-default"
                        title={customBg
                            ? "Opacity of the custom panel color"
                            : "Pick a custom panel color first — presets carry their own opacity"}
                    />
                </div>
                {hasCustom && (
                    <div className="flex items-center justify-between gap-2">
                        <span
                            className="rounded-sm border px-2 py-0.5"
                            style={{
                                background: effective.bg,
                                color: effective.text,
                                borderColor: effective.border,
                            }}
                        >
                            custom preview
                        </span>
                        <button
                            type="button"
                            onClick={resetCustom}
                            className="text-blue-400 hover:text-blue-300 cursor-pointer"
                        >
                            Reset to preset
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default ThemeOptions;
