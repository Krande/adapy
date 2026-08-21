import React from "react";
import {Icon} from "@/components/icons";
import {caretClasses, cn} from "@/components/ui";
import PositionedMenu from "@/components/common/PositionedMenu";
import {useSectionStore} from "@/state/sectionStore";
import {useModelState} from "@/state/modelState";
import {planeConstant, planePosition, sliderRange, sliderStep} from "./sectionRange";

// The active section plane: which one, and where it sits.
//
// This is what the Clip tab had that the toolbar's buttons did not. Add/flip/gizmo/clear
// are actions and became icons; picking WHICH plane you are steering, and dragging it
// along its normal, are not — so folding the tab away without these would have quietly
// removed the two things people actually did in it.
//
// One control rather than two: the button names the active plane and switches between
// them, the slider moves it. They belong together because the slider is meaningless
// without knowing what it is moving, and a toolbar has no room for a label saying so.

export default function SectionPlaneControl() {
    const planes = useSectionStore((s) => s.planes);
    const activeId = useSectionStore((s) => s.activeId);
    const setActive = useSectionStore((s) => s.setActive);
    const setConstant = useSectionStore((s) => s.setConstant);
    const toggle = useSectionStore((s) => s.toggle);
    const removePlane = useSectionStore((s) => s.removePlane);
    const bb = useModelState((s) => s.boundingBox);

    const [open, setOpen] = React.useState(false);
    const btnRef = React.useRef<HTMLButtonElement | null>(null);

    const active = planes.find((p) => p.id === activeId) ?? planes[0] ?? null;

    // Nothing to steer. Rendering a disabled stub would put a dead slider in the strip
    // beside the three buttons that create the thing it needs.
    if (!active) return null;

    const [lo, hi] = sliderRange(active.normal, bb);

    return (
        <span className="inline-flex items-center gap-1">
            <button
                ref={btnRef}
                type="button"
                className={cn(
                    caretClasses("ghost"),
                    // Wide enough for a label; the caret half of a split button is not.
                    "w-auto gap-1 px-1.5 text-content",
                    !active.enabled && "opacity-50",
                )}
                onClick={() => setOpen((v) => !v)}
                title={
                    planes.length > 1
                        ? `Steering ${active.label} — click to switch plane`
                        : `Steering ${active.label}`
                }
                aria-haspopup="menu"
                aria-expanded={open}
            >
                <span className="max-w-24 truncate">{active.label}</span>
                <Icon name="chevron" size="sm" className="rotate-90 shrink-0" />
            </button>

            {open && (
                <PositionedMenu
                    anchor={{kind: "rect", getRect: () => btnRef.current?.getBoundingClientRect()}}
                    ignoreOutsideRef={btnRef}
                    onClose={() => setOpen(false)}
                    items={[
                        // The planes themselves. Clicking one steers it — the same
                        // "select, then act" order the rest of the strip uses, where
                        // flip and the gizmo apply to whichever plane is active.
                        ...planes.map((p) => ({
                            key: p.id,
                            label: `${p.id === active.id ? "✓ " : "   "}${p.label}${p.enabled ? "" : " (off)"}`,
                            onClick: () => {
                                setActive(p.id);
                                setOpen(false);
                            },
                        })),
                        {
                            key: "toggle",
                            label: active.enabled ? `Disable ${active.label}` : `Enable ${active.label}`,
                            onClick: () => {
                                toggle(active.id);
                                setOpen(false);
                            },
                        },
                        {
                            key: "remove",
                            label: `Delete ${active.label}`,
                            onClick: () => {
                                removePlane(active.id);
                                setOpen(false);
                            },
                        },
                    ]}
                />
            )}

            <input
                type="range"
                className="h-control-sm w-28 shrink-0 accent-accent"
                min={lo}
                max={hi}
                step={sliderStep(lo, hi)}
                value={planePosition(active.constant)}
                disabled={!active.enabled}
                onChange={(e) => setConstant(active.id, planeConstant(Number(e.target.value)))}
                aria-label={`Position of ${active.label}`}
                title={`Slide ${active.label} along its normal`}
            />
        </span>
    );
}
