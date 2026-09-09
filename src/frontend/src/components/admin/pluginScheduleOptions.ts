// Validation for a plugin-job schedule's options document.
//
// Its own module, free of React and of viewerApi, for the same reason
// adminTabs.ts is: this is the part of the panel with real rules, and it should
// be testable without a bundler.

/** Options key the scheduler stamps with each firing's timestamp, which the API
 * therefore refuses in a schedule's own options.
 *
 * WHY THE SERVER STAMPS IT. Core hashes a plugin job's options into its source
 * key so an identical repeat request cache-hits a finished job — right for a user
 * pressing a button twice, and catastrophic for a schedule: byte-identical
 * options every hour would mean the second firing and every one after returns the
 * FIRST run's summary. Hourly green ticks, the worker never touched, and a
 * consumer trusting data that stopped moving.
 *
 * Mirrored here only so the form can say why before a round trip. The refusal is
 * the server's, and it is a 400 either way. */
export const RESERVED_OPTIONS_KEY = "scheduled_at";

export type ParsedOptions = {options: Record<string, unknown>} | {error: string};

/** Parse the options textarea: the object, or the reason it is not one.
 *
 * Checked in the browser as well as on the server because this is the one field
 * with no structure a control can enforce. A stray trailing comma would
 * otherwise come back as a 400 after the admin has already filled in the rest of
 * the form, and the message they would get describes JSON, not what to fix. */
export function parseScheduleOptions(text: string): ParsedOptions {
    const trimmed = text.trim();
    // An empty box means "no options", not "malformed": a plugin whose job takes
    // no arguments is the simplest thing to schedule and must not need `{}`.
    if (!trimmed) return {options: {}};
    let parsed: unknown;
    try {
        parsed = JSON.parse(trimmed);
    } catch (e) {
        return {error: `not valid JSON: ${(e as Error).message}`};
    }
    // An array parses and would even survive JSON.stringify on the wire, but the
    // API takes a mapping — caught here so the message names the shape.
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        return {error: 'must be a JSON object, e.g. {"action": "changes"}'};
    }
    if (RESERVED_OPTIONS_KEY in (parsed as Record<string, unknown>)) {
        return {
            error:
                `${RESERVED_OPTIONS_KEY} is reserved — the scheduler stamps it on every firing ` +
                "so that two firings never share an options hash and cache-hit",
        };
    }
    return {options: parsed as Record<string, unknown>};
}
