import React, {Suspense} from "react";
import {create} from "zustand";
import {Dialog, Input, cn} from "@/components/ui";
import {Icon, type IconName} from "@/components/icons";
import {runtime} from "@/runtime/config";
import PointSizeOptions from "./PointSizeOptions";
import DisplayOptions from "./DisplayOptions";
import ExperimentalOptions from "./ExperimentalOptions";
import PerformanceOptions from "./PerformanceOptions";
import ThemeOptions from "./ThemeOptions";

// Settings, as a dialog rather than a dock panel.
//
// It was a panel, which was wrong in three ways at once. It read "Show preferences" in
// the File menu, because panel commands are generated with a Show/Hide prefix — right
// for something you park beside the model, meaningless for a destination you open, use
// and close. It inherited the panel theme, so on the default glass preset the settings
// were translucent over the 3D view. And it competed for dock space with panels you
// actually want open while working.
//
// The shape follows PyCharm's Settings: search at the top, categories down the left, the
// selected category's controls on the right. That layout scales — this already has five
// groups and will get more — where a single scrolling column of disclosures does not.

const RestSection = React.lazy(() => import("./RestSection"));

interface SettingsState {
    open: boolean;
    setOpen: (open: boolean) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
    open: false,
    setOpen: (open) => set({open}),
}));

export const openSettings = () => useSettingsStore.getState().setOpen(true);

interface Category {
    id: string;
    label: string;
    icon: IconName;
    /**
     * Words that should find this page.
     *
     * Hand-maintained, and that is a known weakness: PyCharm searches the actual setting
     * labels, which it can do because every setting is declared data. Ours are JSX, so a
     * true index would mean restructuring every option. Category-level search is the
     * honest subset — it gets you to the right page, which is most of the value — and the
     * keywords name the settings people would actually type.
     */
    keywords: string;
    available?: () => boolean;
    render: () => React.ReactNode;
}

const CATEGORIES: Category[] = [
    {
        id: "scene",
        label: "Scene",
        icon: "scene",
        keywords: "point size absolute colour legend geometry edges tessellation mesh stats auto convert upload fit view lock translation",
        render: () => (
            <div className="flex flex-col gap-5">
                <PointSizeOptions />
                <DisplayOptions />
            </div>
        ),
    },
    {
        id: "theme",
        label: "Theme",
        icon: "view",
        keywords: "colour color panel background text opacity dark light preset gallery",
        render: () => <ThemeOptions />,
    },
    {
        id: "performance",
        label: "Performance",
        icon: "component",
        keywords: "fps draw calls material lambert antialias msaa pixel ratio dpr shadow render picking gpu load time-sliced beam solids edges metrics",
        render: () => <PerformanceOptions />,
    },
    {
        id: "conversion",
        label: "Conversion engine",
        icon: "convert",
        keywords: "wasm pyodide in-browser convert engine experimental",
        available: () => runtime.isRestMode(),
        render: () => <ExperimentalOptions />,
    },
    {
        id: "account",
        label: "Account & scope",
        icon: "server",
        keywords: "sign out user id oidc sub scope project admin convert files",
        available: () => runtime.isRestMode(),
        render: () => (
            <Suspense fallback={null}>
                <RestSection />
            </Suspense>
        ),
    },
];

/** Pure so the ranking is testable: a page matches its label or its keywords. */
export function matchCategories<T extends {label: string; keywords: string}>(
    all: T[],
    query: string,
): T[] {
    const q = query.trim().toLowerCase();
    if (!q) return all;
    // Every word must appear somewhere, so "point size" does not match a page that only
    // mentions "size" — an AND search is what makes a two-word query useful.
    const words = q.split(/\s+/);
    return all.filter((c) => {
        const hay = `${c.label} ${c.keywords}`.toLowerCase();
        return words.every((w) => hay.includes(w));
    });
}

function BuildLine() {
    const version = runtime.adapyVersion();
    const sha = runtime.frontendSha();
    const tag = runtime.viewerImageTag();
    const commit = sha || (tag.startsWith("sha-") ? tag.slice(4) : "");
    const label = version ? (commit ? `${version} (${commit})` : version) : commit || String(runtime.uniqueVersionId());
    return (
        <span className="text-xs text-content-subtle">
            Build <span className="font-mono">{label}</span>
        </span>
    );
}

export default function SettingsDialog() {
    const open = useSettingsStore((s) => s.open);
    const setOpen = useSettingsStore((s) => s.setOpen);
    const [query, setQuery] = React.useState("");
    const [active, setActive] = React.useState("scene");

    const available = CATEGORIES.filter((c) => c.available?.() ?? true);
    const matches = matchCategories(available, query);

    // Searching to a page you cannot see is worse than not searching. If the current
    // page falls out of the results, move to the first that survives.
    React.useEffect(() => {
        if (matches.length && !matches.some((c) => c.id === active)) setActive(matches[0].id);
    }, [query, matches, active]);

    const current = available.find((c) => c.id === active);

    return (
        <Dialog
            open={open}
            onClose={() => setOpen(false)}
            title="Settings"
            width="max-w-4xl"
            footer={<BuildLine />}
        >
            <div className="flex min-h-[26rem] gap-4">
                {/* Sidebar: search + pages. Fixed width so the content pane does not
                    reflow as page names change length. */}
                <div className="flex w-56 shrink-0 flex-col gap-2 border-r border-edge pr-3">
                    <Input
                        fieldSize="sm"
                        autoFocus
                        value={query}
                        placeholder="Search settings"
                        onChange={(e) => setQuery(e.target.value)}
                        aria-label="Search settings"
                    />
                    <nav aria-label="Settings pages" className="flex flex-col gap-0.5">
                        {matches.map((c) => (
                            <button
                                key={c.id}
                                type="button"
                                aria-current={c.id === active ? "page" : undefined}
                                onClick={() => setActive(c.id)}
                                className={cn(
                                    "ada-focus flex items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm",
                                    c.id === active
                                        ? "bg-accent-subtle text-accent"
                                        : "text-content-muted pointer-fine:hover:bg-surface-2 pointer-fine:hover:text-content",
                                )}
                            >
                                <Icon name={c.icon} size="sm" />
                                <span className="truncate">{c.label}</span>
                            </button>
                        ))}
                        {matches.length === 0 && (
                            <p className="px-2 py-3 text-xs text-content-subtle">
                                Nothing matches “{query}”.
                            </p>
                        )}
                    </nav>
                </div>

                <div className="min-w-0 flex-1 overflow-y-auto scrollbar pr-1">
                    {current ? (
                        <>
                            <h3 className="mb-3 text-sm font-semibold text-content">{current.label}</h3>
                            {current.render()}
                        </>
                    ) : null}
                </div>
            </div>
        </Dialog>
    );
}
