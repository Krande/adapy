import React from "react";
import {Icon, Input, Kbd} from "@/components/ui";
import {buildCommands} from "./commands";
import {filterCommands, type Command} from "./commandFilter";
import {Z} from "./zIndex";

/** Lets any surface ask for the palette without owning its state. */
export const OPEN_PALETTE_EVENT = "ada-open-command-palette";
export const openCommandPalette = () => window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT));

// Ctrl+K — the direct answer to "features exist but users don't know they're there".
//
// The old UI's discoverability model was ten identical icon buttons and a drawer: if you
// did not already know a feature existed, nothing would tell you. The palette inverts
// that — type roughly what you want and the app tells you what it can do and which key
// does it, which also teaches the shortcuts rather than hiding them in a modal.
//
// Commands are GENERATED from the panel, mode and shortcut registries (see commands.ts),
// so this list cannot go stale the way a hand-written one would.

export default function CommandPalette() {
    const [open, setOpen] = React.useState(false);
    const [query, setQuery] = React.useState("");
    const [active, setActive] = React.useState(0);
    const inputRef = React.useRef<HTMLInputElement | null>(null);
    const listRef = React.useRef<HTMLUListElement | null>(null);

    // Built when opened, not on every keystroke: the command set depends on mode and
    // layout, both of which are stable while the palette is up.
    const [commands, setCommands] = React.useState<Command[]>([]);
    const results = React.useMemo(() => filterCommands(commands, query), [commands, query]);

    const close = React.useCallback(() => {
        setOpen(false);
        setQuery("");
        setActive(0);
    }, []);

    // Ctrl/Cmd+K toggles. Registered on the window in the capture phase so a focused
    // input inside a panel cannot swallow it — unlike the view shortcuts, the palette
    // must be reachable from anywhere, including mid-typing.
    React.useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            const mod = e.ctrlKey || e.metaKey;
            // Ctrl+K is the primary; Ctrl+Shift+P is the VS Code convention and a
            // fallback for environments where the browser claims Ctrl+K first.
            const isPalette =
                (mod && !e.shiftKey && e.key.toLowerCase() === "k") ||
                (mod && e.shiftKey && e.key.toLowerCase() === "p");
            if (isPalette) {
                e.preventDefault();
                setOpen((v) => !v);
            }
        };
        // Any surface can also ask for the palette by event — the title-bar button uses
        // this rather than reaching into component state.
        const onOpenRequest = () => setOpen(true);
        window.addEventListener("keydown", onKey, true);
        window.addEventListener(OPEN_PALETTE_EVENT, onOpenRequest);
        return () => {
            window.removeEventListener("keydown", onKey, true);
            window.removeEventListener(OPEN_PALETTE_EVENT, onOpenRequest);
        };
    }, []);

    React.useEffect(() => {
        if (!open) return;
        setCommands(buildCommands());
        // Focus after paint so the input exists.
        const id = window.setTimeout(() => inputRef.current?.focus(), 0);
        return () => window.clearTimeout(id);
    }, [open]);

    // Keep the highlighted row in view when arrowing past the fold.
    React.useEffect(() => {
        const el = listRef.current?.children[active] as HTMLElement | undefined;
        el?.scrollIntoView({block: "nearest"});
    }, [active]);

    if (!open) return null;

    const run = (cmd: Command | undefined) => {
        if (!cmd) return;
        // Close FIRST: a command that changes layout or mode re-renders the tree beneath
        // us, and running it while the overlay is still mounted makes the change land
        // behind a scrim the user then has to dismiss.
        close();
        try {
            cmd.run();
        } catch (err) {
            console.warn(`[palette] command "${cmd.id}" threw`, err);
        }
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Escape") {
            e.preventDefault();
            close();
        } else if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((i) => (results.length ? (i + 1) % results.length : 0));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0));
        } else if (e.key === "Enter") {
            e.preventDefault();
            run(results[active]);
        }
    };

    return (
        <div
            style={{zIndex: Z.dialog}}
            className="fixed inset-0 flex items-start justify-center pt-[12vh] bg-black/40"
            onPointerDown={(e) => {
                if (e.target === e.currentTarget) close();
            }}
        >
            <div
                role="dialog"
                aria-modal="true"
                aria-label="Command palette"
                onKeyDown={onKeyDown}
                className="flex flex-col w-full max-w-xl max-h-[60vh] mx-4 bg-surface-1 border border-edge rounded-lg shadow-float overflow-hidden"
            >
                <div className="flex items-center gap-2 shrink-0 px-3 py-2 border-b border-edge">
                    <Icon name="search" size="sm" className="text-content-subtle" />
                    <Input
                        ref={inputRef}
                        value={query}
                        onChange={(e) => {
                            setQuery(e.target.value);
                            setActive(0);
                        }}
                        placeholder="Search commands, panels and modes…"
                        aria-label="Search commands"
                        aria-controls="palette-results"
                        aria-activedescendant={results[active] ? `cmd-${results[active].id}` : undefined}
                        className="border-0 bg-transparent focus:outline-none"
                    />
                    <Kbd>Esc</Kbd>
                </div>

                <ul id="palette-results" role="listbox" ref={listRef} className="flex-1 min-h-0 overflow-auto scrollbar p-1">
                    {results.map((cmd, i) => (
                        <li
                            key={cmd.id}
                            id={`cmd-${cmd.id}`}
                            role="option"
                            aria-selected={i === active}
                            onPointerEnter={() => setActive(i)}
                            onPointerDown={(e) => {
                                e.preventDefault();
                                run(cmd);
                            }}
                            className={
                                "flex items-center gap-2 px-2 h-8 rounded-sm cursor-pointer text-sm " +
                                (i === active ? "bg-accent-subtle text-content" : "text-content-muted")
                            }
                        >
                            {cmd.icon && <Icon name={cmd.icon} size="sm" />}
                            <span className="flex-1 truncate text-content">{cmd.title}</span>
                            {cmd.context && <span className="text-xs text-content-subtle">{cmd.context}</span>}
                            {cmd.keys && <Kbd>{cmd.keys}</Kbd>}
                        </li>
                    ))}
                    {results.length === 0 && (
                        <li className="px-2 py-6 text-center text-sm text-content-subtle">
                            Nothing matches “{query}”.
                        </li>
                    )}
                </ul>
            </div>
        </div>
    );
}
