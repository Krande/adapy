import React from "react";
import {create} from "zustand";
import {Dialog, Kbd} from "@/components/ui";
import {runtime} from "@/runtime/config";
import {SHORTCUTS, type Shortcut} from "./shortcuts";

// Help ▸ Keyboard shortcuts, and Help ▸ About.
//
// The classic UI's ShortcutsModal keeps its own hand-maintained copy of the shortcut
// list — a second source of truth that has to be edited whenever a key changes, and
// therefore the one that goes stale. This renders `shortcuts.ts` directly, so a key that
// is bound is a key that is documented, and `docs/SHORTCUTS.md` is generated from the
// same array.

interface HelpState {
    open: null | "shortcuts" | "about";
    show: (which: "shortcuts" | "about") => void;
    close: () => void;
}

export const useHelpStore = create<HelpState>((set) => ({
    open: null,
    show: (which) => set({open: which}),
    close: () => set({open: null}),
}));

export const showShortcuts = () => useHelpStore.getState().show("shortcuts");
export const showAbout = () => useHelpStore.getState().show("about");

/** Human-readable scope names — `scope` is a code word, these are what a user reads. */
const SCOPE_LABEL: Record<Shortcut["scope"], string> = {
    global: "Anywhere in the viewer",
    builder: "While the procedural builder is open",
    gallery: "While stepping through a gallery",
    shell: "Anywhere, including while typing",
};

function ShortcutTable() {
    // Grouped by scope rather than by the `group` field: what a user needs to know first
    // is WHEN a key is live. A key that only works in the builder listed beside one that
    // works everywhere is how people conclude a shortcut is broken.
    const byScope = new Map<Shortcut["scope"], Shortcut[]>();
    for (const s of SHORTCUTS) {
        const list = byScope.get(s.scope) ?? [];
        list.push(s);
        byScope.set(s.scope, list);
    }

    return (
        <div className="flex flex-col gap-5">
            {[...byScope.entries()].map(([scope, list]) => (
                <section key={scope}>
                    <h3 className="text-sm font-semibold text-accent">{SCOPE_LABEL[scope]}</h3>
                    <ul className="mt-2 divide-y divide-edge rounded-md border border-edge">
                        {list.map((s) => (
                            <li key={s.id} className="flex items-center justify-between gap-4 px-3 py-1.5">
                                <span className="text-sm text-content">{s.label}</span>
                                <span className="flex shrink-0 items-center gap-1">
                                    {s.keys.split("+").map((k, i) => (
                                        <React.Fragment key={i}>
                                            {i > 0 && <span className="text-content-subtle">+</span>}
                                            <Kbd>{k}</Kbd>
                                        </React.Fragment>
                                    ))}
                                </span>
                            </li>
                        ))}
                    </ul>
                </section>
            ))}
        </div>
    );
}

function AboutBody() {
    const version = runtime.adapyVersion();
    const sha = runtime.frontendSha();
    const tag = runtime.viewerImageTag();
    const rows: [string, string][] = [
        ["Version", version || "dev"],
        ["Commit", sha || (tag.startsWith("sha-") ? tag.slice(4) : "—")],
        ["Mode", runtime.isRestMode() ? "Hosted (REST)" : "Desktop / embedded"],
    ];
    return (
        <div className="flex flex-col gap-3">
            <p className="text-content">
                The adapy viewer — inspect models, post-process results, author geometry, and move
                data in and out.
            </p>
            <dl className="flex flex-col gap-1">
                {rows.map(([k, v]) => (
                    <div key={k} className="flex items-baseline gap-2">
                        <dt className="w-24 shrink-0 text-xs text-content-muted">{k}</dt>
                        <dd className="font-mono text-xs text-content">{v}</dd>
                    </div>
                ))}
            </dl>
        </div>
    );
}

export default function HelpDialogs() {
    const open = useHelpStore((s) => s.open);
    const close = useHelpStore((s) => s.close);

    return (
        <>
            <Dialog
                open={open === "shortcuts"}
                onClose={close}
                title="Keyboard shortcuts"
                width="max-w-3xl"
            >
                <ShortcutTable />
            </Dialog>
            <Dialog open={open === "about"} onClose={close} title="About adapy">
                <AboutBody />
            </Dialog>
        </>
    );
}
