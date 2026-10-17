import React, {useState} from "react";

// One top-level section of an admin tab, drawn as a card: a shaded header with the section's
// title, a chip naming what KIND of section it is, and room for actions, over a bordered body.
//
// WHY A CARD. The Providers tab stacks sections of different kinds -- one per asset provider, the
// joint detailing spec providers, the scheduled jobs -- and as rows separated by a hairline they
// read as one long list. A border, a header band and the kind chip are what let the eye find where
// one ends.
//
// Collapsible, and the choice is remembered per browser under `storageKey`: a viewer convenience,
// so local storage (wrapped -- it can be missing or refused, and the section then simply opens).

export function readOpen(storageKey: string | undefined, fallback: boolean): boolean {
    if (!storageKey) return fallback;
    try {
        const raw = localStorage.getItem(storageKey);
        return raw === null ? fallback : raw === "1";
    } catch {
        return fallback;
    }
}

export function writeOpen(storageKey: string | undefined, open: boolean): void {
    if (!storageKey) return;
    try {
        localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
        // Not remembered; still applied for this visit.
    }
}

const AdminSection: React.FC<{
    title: React.ReactNode;
    /** What kind of section this is, e.g. "Asset provider". */
    kind?: string;
    /** Under the title, in the header. */
    subtitle?: React.ReactNode;
    /** Right-aligned in the header; clicks there do not fold the section. */
    actions?: React.ReactNode;
    storageKey?: string;
    defaultOpen?: boolean;
    testId?: string;
    children: React.ReactNode;
}> = ({title, kind, subtitle, actions, storageKey, defaultOpen = true, testId, children}) => {
    const [open, setOpen] = useState(() => readOpen(storageKey, defaultOpen));
    const toggle = () => {
        writeOpen(storageKey, !open);
        setOpen(!open);
    };
    return (
        <section className="mx-3 my-3 rounded-md border border-gray-700 bg-gray-900/40 overflow-hidden" data-testid={testId}>
            <div className="flex items-start gap-2 bg-gray-800/80 px-3 py-2 border-b border-gray-700">
                <button
                    type="button"
                    className="flex min-w-0 flex-1 items-start gap-2 text-left"
                    aria-expanded={open}
                    title={open ? "Fold this section" : "Show this section"}
                    onClick={toggle}
                >
                    <span className="mt-0.5 text-gray-500">{open ? "▾" : "▸"}</span>
                    <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-gray-100">{title}</span>
                            {kind && (
                                <span className="rounded-sm border border-gray-600 bg-gray-900/60 px-1.5 py-px text-[10px] uppercase tracking-wide text-gray-400">
                                    {kind}
                                </span>
                            )}
                        </span>
                        {subtitle && <span className="block text-xs text-gray-400">{subtitle}</span>}
                    </span>
                </button>
                {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
            </div>
            {open && <div>{children}</div>}
        </section>
    );
};

export default AdminSection;
