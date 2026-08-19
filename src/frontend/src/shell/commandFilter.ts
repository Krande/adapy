import type {IconName} from "@/components/icons";

// Command shape and ranking — the pure half of the palette.
//
// Separated from commands.ts for the same reason coreProviderRules is separated from
// registerCoreProviders: the wiring half imports the action modules, which reach
// cellBuilderStore, which reaches a vite `?worker&inline` module only a bundler can
// resolve. Keeping the ranking here makes it testable under plain `node --test` — and
// ranking is the half users actually feel, since typing three letters and getting the
// wrong first result is what makes a palette not get used twice.

export interface Command {
    id: string;
    title: string;
    /** Shown after the title — the mode or panel a command belongs to. */
    context?: string;
    icon?: IconName;
    keys?: string;
    /** Extra words to match on that are not in the title (synonyms, old names). */
    keywords?: string;
    run: () => void;
}

/**
 * Rank a command against a query. Lower is better; null means no match.
 *
 * Field order matters: a command NAMED for what you typed must beat one that merely
 * mentions it as a synonym. Within a field, a prefix beats a word-start beats a
 * mid-word hit, so exact typing wins over incidental letter runs.
 */
export function scoreCommand(cmd: Command, query: string): number | null {
    const q = query.trim().toLowerCase();
    if (!q) return 0;

    const haystacks = [cmd.title, cmd.context ?? "", cmd.keywords ?? ""].map((h) => h.toLowerCase());

    for (let i = 0; i < haystacks.length; i++) {
        const h = haystacks[i];
        // Field weight: title (0) beats context (40) beats keywords (60).
        const weight = i === 0 ? 0 : i === 1 ? 40 : 60;
        const at = h.indexOf(q);
        if (at === 0) return weight;
        if (at > 0) return weight + (h[at - 1] === " " ? 5 : 15);
    }

    // Subsequence fallback on the title only — cheap initialism tolerance, so "fta"
    // finds "Fit all to view". Scored worst so it never displaces a real substring hit.
    const title = haystacks[0];
    let ti = 0;
    for (const ch of q) {
        ti = title.indexOf(ch, ti);
        if (ti === -1) return null;
        ti++;
    }
    return 80;
}

/** Matching commands, best first. A stable sort keeps registry order within a tie. */
export function filterCommands(commands: Command[], query: string): Command[] {
    return commands
        .map((c, i) => ({c, s: scoreCommand(c, query), i}))
        .filter((x): x is {c: Command; s: number; i: number} => x.s !== null)
        .sort((a, b) => a.s - b.s || a.i - b.i)
        .map((x) => x.c);
}
