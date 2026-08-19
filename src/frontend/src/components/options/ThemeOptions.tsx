import React from "react";
import {Button, PropertyRow, Slider, Switch, cn} from "@/components/ui";
import {
    effectivePanelTheme,
    THEME_PRESETS,
    ThemePresetId,
    useThemeStore,
} from "@/state/themeStore";
import {useGalleryStore} from "@/state/galleryStore";

// Theme picker for the panel chrome. Four preset cards (each a live mini-preview of its
// own chrome) plus custom swatches for panel background + text and an opacity slider, for
// landing anywhere between "max legibility" and "don't distract from the 3D view".
//
// Re-chromed, with one deliberate exception: the preset cards keep their inline
// `style={{background: p.theme.bg}}`. That is not ad-hoc styling — it is the preview, and
// the whole point is that a card looks like the thing it selects.

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

    const galleryEnabled = useGalleryStore((s) => s.enabled);
    const setGalleryEnabled = useGalleryStore((s) => s.setEnabled);

    const hasCustom = customBg !== null || customText !== null;
    const effective = effectivePanelTheme({preset, customBg, customText, bgOpacity});

    return (
        <div className="flex flex-col gap-3">
            <Switch
                label="Gallery mode"
                hint="Prev/next HUD cycling this scope's files"
                checked={galleryEnabled}
                onChange={(e) => setGalleryEnabled(e.target.checked)}
            />

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
                            aria-pressed={active}
                            className={cn(
                                "ada-focus rounded-md border px-2 py-1.5 text-left text-xs cursor-pointer",
                                "transition-colors duration-(--ada-dur-fast)",
                                active ? "border-accent ring-1 ring-accent" : "border-edge pointer-fine:hover:border-edge-strong",
                            )}
                            // The card IS the preview — these come from the preset data.
                            style={{background: p.theme.bg, color: p.theme.text}}
                        >
                            <div className="font-semibold">{p.name}</div>
                            <div className="opacity-80">Aa panel text</div>
                        </button>
                    );
                })}
            </div>

            <div className="flex flex-col gap-1.5">
                <PropertyRow label="Panel colour">
                    <input
                        type="color"
                        // Colour inputs need a hex; with no override set, show a neutral
                        // derived from the active preset family.
                        value={customBg ?? "#111827"}
                        onChange={(e) => setCustomBg(e.target.value)}
                        className="ada-focus h-6 w-10 cursor-pointer rounded-sm border border-edge bg-transparent"
                        title="Custom panel background"
                    />
                </PropertyRow>
                <PropertyRow label="Text colour">
                    <input
                        type="color"
                        value={customText ?? "#f3f4f6"}
                        onChange={(e) => setCustomText(e.target.value)}
                        className="ada-focus h-6 w-10 cursor-pointer rounded-sm border border-edge bg-transparent"
                        title="Custom panel text colour"
                    />
                </PropertyRow>
                <PropertyRow
                    label={<span className={cn(!customBg && "opacity-50")}>Panel opacity</span>}
                    hint={customBg ? undefined : "Pick a custom panel colour first — presets carry their own opacity"}
                >
                    <div className="w-32">
                        <Slider
                            min={0.1}
                            max={1}
                            step={0.05}
                            value={bgOpacity}
                            disabled={!customBg}
                            readout
                            format={(n) => n.toFixed(2)}
                            onValueChange={(n) => setBgOpacity(n)}
                        />
                    </div>
                </PropertyRow>

                {hasCustom && (
                    <div className="flex items-center justify-between gap-2 pt-1">
                        <span
                            className="rounded-sm border px-2 py-0.5 text-xs"
                            // Live preview of the custom combination — data, not chrome.
                            style={{background: effective.bg, color: effective.text, borderColor: effective.border}}
                        >
                            custom preview
                        </span>
                        <Button size="sm" variant="subtle" onClick={resetCustom}>
                            Reset to preset
                        </Button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default ThemeOptions;
