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

// A MULTIPLE setting (`"multiple": true`, an enum with choices) holds a LIST of its choices and is
// drawn as one checkbox per choice. Generic: any provider's multiple setting, whatever its name.
// Unset, it holds the setting's declared default (apply-changes: every target ticked), so a form
// that never touched it still sends the full list -- and ticking none is refused before sending.

/** The subset of a setting these helpers read (a `ScheduleSetting` fits). */
export interface MultiSettingLike {
    name: string;
    title?: string;
    multiple?: boolean;
    default?: unknown;
    choices?: ScheduleChoice[];
}

export function isMultiSetting(s: MultiSettingLike): boolean {
    return s.multiple === true && (s.choices?.length ?? 0) > 0;
}

/** The choices `value` names, in the choices' own order; anything that is not a choice is dropped. */
export function ticked(value: unknown, choices: ScheduleChoice[]): unknown[] {
    const list = Array.isArray(value) ? value : [];
    return choices.filter((c) => list.some((v) => sameChoice(v, c.value))).map((c) => c.value);
}

/** What a multiple setting holds while the form has not set it: its declared default, else every choice. */
export function multiDefault(s: MultiSettingLike): unknown[] {
    const choices = s.choices ?? [];
    return Array.isArray(s.default) ? ticked(s.default, choices) : choices.map((c) => c.value);
}

/** The ticked values to show for a multiple setting: the held list, or the default when unset. */
export function multiValue(s: MultiSettingLike, value: unknown): unknown[] {
    return value === undefined || value === null ? multiDefault(s) : ticked(value, s.choices ?? []);
}

/** `value` with `choice` ticked or unticked. */
export function toggleChoice(s: MultiSettingLike, value: unknown, choice: unknown, on: boolean): unknown[] {
    const choices = s.choices ?? [];
    const now = multiValue(s, value).filter((v) => !sameChoice(v, choice));
    return ticked(on ? [...now, choice] : now, choices);
}

/** `settings` with every multiple setting spelled out (an unset one as its default): what is sent. */
export function withMultiDefaults(settingList: MultiSettingLike[], settings: Record<string, unknown>): Record<string, unknown> {
    const out = {...settings};
    for (const s of settingList) if (isMultiSetting(s)) out[s.name] = multiValue(s, settings[s.name]);
    return out;
}

/** The inline refusal when a multiple setting has nothing ticked, else null. */
export function multiSettingError(settingList: MultiSettingLike[], settings: Record<string, unknown>): string | null {
    for (const s of settingList) {
        if (isMultiSetting(s) && multiValue(s, settings[s.name]).length === 0) {
            return `Tick at least one under "${s.title ?? s.name}".`;
        }
    }
    return null;
}
