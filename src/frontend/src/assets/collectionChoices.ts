// Per-collection choices an admin may override on a provider job -- e.g. which of several copies
// (a "share") of a project the worker reads it from. Generic: a provider declares
//
//     asset_collection_choices: [{option: "project_root", label: "Share", field: "project_locations"}]
//
// and in that field, keyed by collection, the choices its worker found (only collections with two or
// more): `{ABC: {default: v, choices: [{value: v, label: "..."}, ...]}}`. Core's listing hands the
// same shape back merged over every worker (`collection_choices` on a provider). A run or schedule
// stores only picks that differ from the worker's default (`collection_choices: {option:
// {collection: value}}`); with no pick, the worker decides. Pure: tested without a DOM.

export interface CollectionChoice {
    value: unknown;
    label: string;
}

export interface CollectionChoiceEntry {
    default: unknown;
    choices: CollectionChoice[];
}

export interface CollectionChoiceDecl {
    option: string;
    label: string;
    collections: Record<string, CollectionChoiceEntry>;
}

/** `{option: {collection: value}}` -- only overrides of the worker's default. */
export type CollectionPicks = Record<string, Record<string, unknown>>;

/** The settings key core stores the picks under. */
export const COLLECTION_CHOICES = "collection_choices";

function isRecord(v: unknown): v is Record<string, unknown> {
    return typeof v === "object" && v !== null && !Array.isArray(v);
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function readEntry(v: unknown): CollectionChoiceEntry | null {
    if (!isRecord(v) || !Array.isArray(v.choices)) return null;
    const choices = v.choices
        .filter((c): c is Record<string, unknown> => isRecord(c) && "value" in c)
        .map((c) => ({value: c.value, label: typeof c.label === "string" && c.label ? c.label : String(c.value)}));
    return choices.length >= 2 ? {default: v.default ?? null, choices} : null;
}

/** Core's listing shape (`collection_choices` on a provider), checked; malformed parts dropped. */
export function parseCollectionChoiceDecls(raw: unknown): CollectionChoiceDecl[] {
    if (!Array.isArray(raw)) return [];
    const out: CollectionChoiceDecl[] = [];
    for (const d of raw) {
        if (!isRecord(d) || typeof d.option !== "string" || !d.option) continue;
        const collections: Record<string, CollectionChoiceEntry> = {};
        for (const [c, v] of Object.entries(isRecord(d.collections) ? d.collections : {})) {
            const e = readEntry(v);
            if (e) collections[c] = e;
        }
        out.push({option: d.option, label: typeof d.label === "string" && d.label ? d.label : d.option, collections});
    }
    return out;
}

/** The same, read straight off a plugin spec (the Sources tab has the spec, not core's listing). */
export function collectionChoiceDeclsOfSpec(spec: unknown): CollectionChoiceDecl[] {
    if (!isRecord(spec) || !Array.isArray(spec.asset_collection_choices)) return [];
    return parseCollectionChoiceDecls(
        spec.asset_collection_choices
            .filter((d): d is Record<string, unknown> => isRecord(d) && typeof d.field === "string")
            .map((d) => ({option: d.option, label: d.label, collections: spec[d.field as string]})),
    );
}

/** The declaration's entry for `collection`, matched case-insensitively. */
export function entryFor(decl: CollectionChoiceDecl, collection: string): CollectionChoiceEntry | null {
    const want = collection.toLowerCase();
    for (const [c, e] of Object.entries(decl.collections)) if (c.toLowerCase() === want) return e;
    return null;
}

export interface AffectedChoice {
    decl: CollectionChoiceDecl;
    collection: string;
    entry: CollectionChoiceEntry;
}

/** Each (declaration, collection) a form should offer: of `collections` (null: every collection
 *  the provider lists a choice for), those with two or more choices, sorted by collection. */
export function affectedChoices(decls: CollectionChoiceDecl[], collections: string[] | null): AffectedChoice[] {
    const out: AffectedChoice[] = [];
    for (const decl of decls) {
        const names = collections ?? Object.keys(decl.collections);
        for (const collection of names) {
            const entry = entryFor(decl, collection);
            if (entry) out.push({decl, collection, entry});
        }
    }
    return out.sort((a, b) => a.collection.localeCompare(b.collection) || a.decl.option.localeCompare(b.decl.option));
}

/** The pick for (option, collection), case-insensitively; undefined means the worker's default. */
export function pickOf(picks: CollectionPicks | undefined, option: string, collection: string): unknown {
    const per = picks?.[option];
    if (!per) return undefined;
    const key = Object.keys(per).find((c) => c.toLowerCase() === collection.toLowerCase());
    return key === undefined ? undefined : per[key];
}

/** `picks` with (option, collection) set to `value` -- or cleared, when `value` is the worker's
 *  default or empty (no override is stored for the default). */
export function withPick(
    picks: CollectionPicks | undefined,
    option: string,
    collection: string,
    value: unknown,
    entry: CollectionChoiceEntry,
): CollectionPicks {
    const out: CollectionPicks = {};
    for (const [o, per] of Object.entries(picks ?? {})) out[o] = {...per};
    const per = out[option] ?? {};
    for (const k of Object.keys(per)) if (k.toLowerCase() === collection.toLowerCase()) delete per[k];
    if (value !== undefined && value !== null && value !== "" && !same(value, entry.default)) per[collection] = value;
    if (Object.keys(per).length) out[option] = per;
    else delete out[option];
    return out;
}

/** The empty option's label: "(default: <the worker's choice>)". */
export function defaultChoiceLabel(entry: CollectionChoiceEntry): string {
    const hit = entry.choices.find((c) => same(c.value, entry.default));
    return `(default: ${hit ? hit.label : entry.default == null ? "the worker's choice" : String(entry.default)})`;
}

/** A value's label, if the declaration lists it. */
export function choiceLabelOf(decls: CollectionChoiceDecl[], option: string, collection: string, value: unknown): string {
    const decl = decls.find((d) => d.option === option);
    const hit = decl ? entryFor(decl, collection)?.choices.find((c) => same(c.value, value)) : undefined;
    return hit ? hit.label : String(value);
}

/** One line per override: "Share ABC: <label>". Picks for options the provider no longer declares
 *  still show (by name), so nothing stored is hidden. */
export function picksSummary(decls: CollectionChoiceDecl[], picks: CollectionPicks | undefined): string[] {
    const out: string[] = [];
    for (const [option, per] of Object.entries(picks ?? {})) {
        const label = decls.find((d) => d.option === option)?.label ?? option;
        for (const [collection, value] of Object.entries(per)) {
            out.push(`${label} ${collection}: ${choiceLabelOf(decls, option, collection, value)}`);
        }
    }
    return out.sort();
}

/** What a run item's provider job was sent (`collection_choices` on the item, `{option: value}`):
 *  "Share: <label>" for a pick, "Share: the worker's choice" for a collection that has choices but
 *  ran on the worker's own. [] when the item carries no record (the provider declares none). */
export function itemChoiceLines(decls: CollectionChoiceDecl[], collection: string, sent: unknown): string[] {
    if (!isRecord(sent)) return [];
    const out: string[] = [];
    for (const [option, value] of Object.entries(sent)) {
        const label = decls.find((d) => d.option === option)?.label ?? option;
        out.push(`${label}: ${choiceLabelOf(decls, option, collection, value)}`);
    }
    for (const decl of decls) {
        if (!(decl.option in sent) && entryFor(decl, collection)) out.push(`${decl.label}: the worker's choice`);
    }
    return out;
}

/** The options a single-collection request sends for its picks: `{option: value}`. */
export function pickOptions(decls: CollectionChoiceDecl[], picks: CollectionPicks | undefined, collection: string): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const decl of decls) {
        const value = pickOf(picks, decl.option, collection);
        if (value !== undefined && entryFor(decl, collection)) out[decl.option] = value;
    }
    return out;
}

/** The picks stored in a settings object, if any. */
export function picksOfSettings(settings: Record<string, unknown> | undefined): CollectionPicks {
    const raw = settings?.[COLLECTION_CHOICES];
    if (!isRecord(raw)) return {};
    const out: CollectionPicks = {};
    for (const [o, per] of Object.entries(raw)) if (isRecord(per)) out[o] = {...per};
    return out;
}

/** `settings` with the picks set (the key removed when there are none). */
export function settingsWithPicks(settings: Record<string, unknown>, picks: CollectionPicks): Record<string, unknown> {
    const out = {...settings};
    if (Object.keys(picks).length) out[COLLECTION_CHOICES] = picks;
    else delete out[COLLECTION_CHOICES];
    return out;
}
