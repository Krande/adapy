import type {ScheduleChoice} from "@/services/api/assetSchedules";

// A schedule setting's choices carry their DECLARED type: an enum of strings, of numbers, or a
// `bool` setting's `[false, true]`. An HTML <option> value is always text, so the select keys each
// choice by its JSON spelling and maps the picked key back to the declared value -- a bool setting
// sends a real `false`, never the string "false". The server checks (and types) it again; this only
// keeps the form honest.
//
// Matching a stored value against the choices is tolerant of how a bool travelled: `false`,
// "false" and "False" (Python's str(bool), which an older schedule may hold) are all the declared
// `false` -- the same rule as `same_choice` in routes/asset_schedules.py.

const BOOL_WORDS: Record<string, boolean> = {true: true, false: false};

function asBool(v: unknown): boolean | null {
    if (typeof v === "boolean") return v;
    if (typeof v === "string") return BOOL_WORDS[v.trim().toLowerCase()] ?? null;
    return null;
}

/** Whether `value` names the declared `choice`. */
export function sameChoice(value: unknown, choice: unknown): boolean {
    if (typeof value === "boolean" || typeof choice === "boolean") {
        const a = asBool(value);
        return a !== null && a === asBool(choice);
    }
    return String(value) === String(choice);
}

/** The <option> value for a choice: its JSON spelling, so `false` and "false" stay distinct keys. */
export function choiceKey(value: unknown): string {
    return JSON.stringify(value) ?? String(value);
}

/** The key of the choice `value` names, or "" when it names none (the "provider default" row). */
export function selectedKey(value: unknown, choices: ScheduleChoice[]): string {
    if (value == null || value === "") return "";
    const hit = choices.find((c) => sameChoice(value, c.value));
    return hit ? choiceKey(hit.value) : "";
}

/** The declared value an <option> key stands for; `null` for "" (unset) or a key no choice has. */
export function valueOfKey(key: string, choices: ScheduleChoice[]): unknown {
    if (!key) return null;
    const hit = choices.find((c) => choiceKey(c.value) === key);
    return hit ? hit.value : null;
}

/** How a chosen value reads in a summary: its choice's label where one is known. */
export function choiceLabel(value: unknown, choices: ScheduleChoice[] | undefined): string {
    const hit = (choices ?? []).find((c) => sameChoice(value, c.value));
    return hit ? hit.label : String(value);
}
