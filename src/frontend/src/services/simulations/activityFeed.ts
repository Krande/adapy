// A run's activity feed: what each of its actors (a robot, an arm, a solver stage, the mission that
// directs them) was doing and planning, over time. The run artefact with the role `activity`
// (ada.simulation.activity has the format: JSON Lines, a header and then entries, append-only).
//
// Read here as it arrives: the file grows while the run is going, and `append` takes whatever bytes
// came since, cut wherever they were cut. What comes out is what the run's console shows: per actor
// what it is doing and what it plans at a moment, and everything that was said up to that moment.
//
// No React, no store, no three.js: bytes in, answers out.

export const ACTIVITY_SCHEMA = "ada-activity/1";
/** The artefact role a run's feed is stored with. */
export const ACTIVITY_ROLE = "activity";

export type ActivityKind = "state" | "plan" | "event";
export type ActivityLevel = "info" | "warn" | "error";

export interface ActivityActor {
    readonly id: string;
    /** What it is called to a person; its id when the feed gives none. */
    readonly label: string;
    /** A CSS colour, or "" for none of its own. */
    readonly color: string;
}

export interface ActivityEntry {
    /** Seconds on the run's own clock: the playback clock's, for the run on screen. */
    readonly t: number;
    readonly actor: string;
    readonly kind: ActivityKind;
    readonly text: string;
    readonly level: ActivityLevel;
    readonly data?: unknown;
}

const KINDS: readonly string[] = ["state", "plan", "event"];
const LEVELS: readonly string[] = ["info", "warn", "error"];

/** The index of the first of `sorted` (by `t`) that is after `time`: how many are at or before it. */
function countUpTo(sorted: readonly ActivityEntry[], time: number): number {
    let lo = 0;
    let hi = sorted.length;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (sorted[mid].t <= time) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

export class ActivityFeedError extends Error {}

export class ActivityFeed {
    /** Every entry, by time (entries of one time in the order they were written). */
    readonly entries: ActivityEntry[] = [];
    private readonly known = new Map<string, ActivityActor>();
    private readonly states = new Map<string, ActivityEntry[]>();
    private readonly plans = new Map<string, ActivityEntry[]>();
    private decoder = new TextDecoder("utf-8");
    /** The end of what was appended that is not a whole line yet. */
    private pending = "";
    private headed = false;

    /** The actors: those the header lists, in its order, then any an entry named. */
    get actors(): ActivityActor[] {
        return [...this.known.values()];
    }

    /** The time of the last entry; 0 without one. */
    get duration(): number {
        return this.entries.length ? this.entries[this.entries.length - 1].t : 0;
    }

    /**
     * Take the next bytes of the file. Returns how many entries they completed. Throws
     * `ActivityFeedError` when the file's first line is not this format's header; a later line that
     * is not an entry (not JSON, without a time or an actor) is left out, as a file that is still
     * being written may end anywhere.
     */
    append(bytes: Uint8Array): number {
        const text = this.pending + this.decoder.decode(bytes, {stream: true});
        const lines = text.split("\n");
        this.pending = lines.pop() ?? "";
        let added = 0;
        let sorted = true;
        for (const raw of lines) {
            const line = raw.trim();
            if (!line) continue;
            let value: unknown;
            try {
                value = JSON.parse(line);
            } catch {
                if (!this.headed) throw new ActivityFeedError("the run's activity feed does not start with its header");
                continue;
            }
            if (!this.headed) {
                this.head(value);
                continue;
            }
            // Actors may be named after the header too: a recorder learns who they are when the
            // mission says so, which is after its file was begun.
            if (this.name(value)) continue;
            const entry = this.entryOf(value);
            if (!entry) continue;
            if (this.entries.length && entry.t < this.entries[this.entries.length - 1].t) sorted = false;
            this.entries.push(entry);
            this.index(entry);
            added++;
        }
        // Written in order by every writer there is; one that is not is put in order once per batch.
        if (!sorted) this.sort();
        return added;
    }

    private head(value: unknown): void {
        const header = value as {schema?: unknown; actors?: unknown} | null;
        if (!header || typeof header !== "object" || header.schema !== ACTIVITY_SCHEMA) {
            throw new ActivityFeedError(`the run's activity feed is not an ${ACTIVITY_SCHEMA}`);
        }
        this.headed = true;
        this.name(value);
    }

    /** Take the actors a line names (`{"actors": [...]}`, the header among them); false when it names none.
     *  An actor that was only known from an entry gets its label and colour; the order is that of first mention. */
    private name(value: unknown): boolean {
        const line = value as {actors?: unknown; t?: unknown} | null;
        if (!line || typeof line !== "object" || !Array.isArray(line.actors) || typeof line.t === "number") return false;
        for (const a of line.actors as {id?: unknown; label?: unknown; color?: unknown}[]) {
            if (!a || typeof a.id !== "string") continue;
            this.known.set(a.id, {
                id: a.id,
                label: typeof a.label === "string" && a.label ? a.label : a.id,
                color: typeof a.color === "string" ? a.color : "",
            });
        }
        return true;
    }

    private entryOf(value: unknown): ActivityEntry | null {
        const e = value as {t?: unknown; actor?: unknown; kind?: unknown; text?: unknown; level?: unknown; data?: unknown} | null;
        if (!e || typeof e !== "object" || typeof e.actor !== "string" || typeof e.t !== "number" || !Number.isFinite(e.t)) return null;
        return {
            t: e.t,
            actor: e.actor,
            kind: (KINDS.includes(e.kind as string) ? e.kind : "event") as ActivityKind,
            text: typeof e.text === "string" ? e.text : "",
            level: (LEVELS.includes(e.level as string) ? e.level : "info") as ActivityLevel,
            ...(e.data !== undefined ? {data: e.data} : {}),
        };
    }

    private index(entry: ActivityEntry): void {
        if (!this.known.has(entry.actor)) this.known.set(entry.actor, {id: entry.actor, label: entry.actor, color: ""});
        const by = entry.kind === "state" ? this.states : entry.kind === "plan" ? this.plans : null;
        if (!by) return;
        const list = by.get(entry.actor);
        if (list) list.push(entry);
        else by.set(entry.actor, [entry]);
    }

    private sort(): void {
        const byTime = (a: ActivityEntry, b: ActivityEntry): number => a.t - b.t; // stable: ties keep their order
        this.entries.sort(byTime);
        for (const list of this.states.values()) list.sort(byTime);
        for (const list of this.plans.values()) list.sort(byTime);
    }

    /** How many entries are at or before `time`: `entries.slice(0, countUpTo(time))` is what has been said by then. */
    countUpTo(time: number): number {
        return countUpTo(this.entries, time);
    }

    /** What `actor` is doing at `time`: its latest state at or before it. */
    stateAt(actor: string, time: number): ActivityEntry | undefined {
        const list = this.states.get(actor);
        return list ? list[countUpTo(list, time) - 1] : undefined;
    }

    /** What `actor` plans at `time`: its latest plan at or before it, unless that took the plan away (an empty text). */
    planAt(actor: string, time: number): ActivityEntry | undefined {
        const list = this.plans.get(actor);
        const plan = list ? list[countUpTo(list, time) - 1] : undefined;
        return plan && plan.text ? plan : undefined;
    }
}
