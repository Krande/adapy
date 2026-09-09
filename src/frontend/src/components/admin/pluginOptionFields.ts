// Turning a plugin's declared job options into form state, and back into the
// options document the API is sent.
//
// WHY A DECLARATION AND NOT A JSON BOX. Core passes a plugin job's options
// through verbatim and never interprets them, so the only thing that knows what
// they are is the plugin. A textarea is the honest consequence of that — and it
// is also unusable: the admin has to know the key names, the value vocabulary and
// the JSON, and a typo is a 400 at best and a silently ignored option at worst.
//
// So the PLUGIN declares its options and the panel renders them. Nothing in core
// has to learn a vocabulary: `register_plugin_backend(**extra)` folds any key
// into the spec that `GET /plugins` returns, so a plugin can start declaring
// options against an unchanged core. The field names here deliberately match the
// conversion-option descriptors in runtime/config.ts — same vocabulary, so
// neither side has two shapes to keep in step.
//
// React-free and viewerApi-free on purpose: this is where the rules are, and it
// should be testable without a bundler (see adminTabs.ts for the same reasoning).

export type PluginOptionValue = boolean | string | number | string[];

/** One option a plugin advertises for its jobs. */
export interface PluginJobOption {
    name: string;
    type: "bool" | "string" | "int" | "float" | "enum" | "string_list";
    /** Human label. Falls back to `name`, which is what the API receives. */
    title?: string;
    description?: string;
    default?: PluginOptionValue | null;
    /** Choices for `enum`. */
    enum?: readonly string[];
    /** Human label per enum value, so the wire token need not be readable. */
    labels?: Readonly<Record<string, string>>;
    /** Name of the option whose value decides whether this one applies. */
    depends_on?: string;
    /** Values of `depends_on` this option applies to. Absent = all of them.
     *
     * HIDDEN rather than disabled, which is where this parts company with the
     * conversion matrix's `supported_by`. There, a disabled toggle tells the user
     * the capability exists but not for this path. Here the dependency is almost
     * always the action, and an option belonging to a different action is not a
     * thing the user is being denied — it is noise in a form that already has
     * enough of it. */
    applies_to?: readonly string[];
    /** For a dependent enum: the subset of `enum` valid per `depends_on` value. */
    enum_by?: Readonly<Record<string, readonly string[]>>;
    /** Names a key on the plugin's OWN spec to take the choices from.
     *
     * For a choice list that has to be union-merged across workers. A spec is
     * advertised by each worker and one of them wins the duplicate, EXCEPT for the
     * keys named in `union_fields`, which are combined. A declaration cannot be
     * union-merged (merging two copies would duplicate every option), so an enum
     * that must cover the whole fleet — which project, which device, which
     * licence — cannot list its choices inline: it would only ever show what one
     * worker serves. Naming the unioned key instead gets the full set.
     *
     * Resolved into `enum` when the declaration is read; nothing downstream has to
     * know the difference. */
    enum_from?: string;
    /** Refuse to submit while this is empty. */
    required?: boolean;
    placeholder?: string;
}

/** Is `decl` in play, given what is currently selected? */
export function optionApplies(decl: PluginJobOption, values: Record<string, unknown>): boolean {
    if (!decl.depends_on || !decl.applies_to) return true;
    const on = values[decl.depends_on];
    // A dependency on an option that has no value yet leaves this one out: the
    // alternative is showing every branch of every action at once, which is the
    // JSON box with extra steps.
    return typeof on === "string" && decl.applies_to.includes(on);
}

/** The options to render, in declaration order. */
export function visibleOptions(
    decls: readonly PluginJobOption[],
    values: Record<string, unknown>,
): PluginJobOption[] {
    return decls.filter((d) => optionApplies(d, values));
}

/** The choices for one enum, narrowed by `enum_by` when it depends on another. */
export function choicesFor(decl: PluginJobOption, values: Record<string, unknown>): readonly string[] {
    if (decl.enum_by && decl.depends_on) {
        const on = values[decl.depends_on];
        const narrowed = typeof on === "string" ? decl.enum_by[on] : undefined;
        if (narrowed && narrowed.length) return narrowed;
    }
    return decl.enum ?? [];
}

/** Initial form state: the declared defaults, overridden by an existing document.
 *
 * Values arrive from the API as JSON, so a `string_list` is an array and an `int`
 * is a number; the widgets edit text. Normalised to the widget's shape here so
 * every field is controlled from the first render. */
export function seedOptionValues(
    decls: readonly PluginJobOption[],
    existing: Record<string, unknown> = {},
): Record<string, PluginOptionValue> {
    const out: Record<string, PluginOptionValue> = {};
    for (const decl of decls) {
        const has = Object.prototype.hasOwnProperty.call(existing, decl.name);
        const raw = has ? existing[decl.name] : decl.default;
        if (raw === undefined || raw === null) {
            out[decl.name] = decl.type === "bool" ? false : "";
            continue;
        }
        if (decl.type === "bool") {
            out[decl.name] = raw === true || raw === "true";
        } else if (decl.type === "string_list") {
            out[decl.name] = Array.isArray(raw) ? raw.map((v) => String(v)) : String(raw).split(/\s*,\s*/);
        } else {
            out[decl.name] = String(raw);
        }
    }
    return out;
}

/** The part of an existing document that no declaration covers.
 *
 * Surfaced and PRESERVED rather than dropped. A plugin accepts keys it has not
 * declared (a newer worker than the panel's list, a hand-written schedule), and a
 * form that silently discarded them would turn "I changed the cron" into "I also
 * removed two options", with nothing said about it. */
export function pickUndeclaredOptions(
    decls: readonly PluginJobOption[],
    existing: Record<string, unknown> = {},
): Record<string, unknown> {
    const declared = new Set(decls.map((d) => d.name));
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(existing).sort()) {
        if (!declared.has(key)) out[key] = existing[key];
    }
    return out;
}

export type BuiltOptions = {options: Record<string, unknown>} | {error: string};

/** The document to send: every option in play, plus the undeclared keys kept.
 *
 * DEFAULTS ARE SENT, not left implicit. For a one-off job either would do; for a
 * schedule the stored document is the record of what it runs, and an implicit
 * default means the schedule's behaviour changes when the plugin's default does —
 * with no edit to point at.
 *
 * An empty optional field is omitted, so the plugin's own default still applies
 * to anything the admin deliberately left blank. */
export function buildOptionValues(
    decls: readonly PluginJobOption[],
    values: Record<string, unknown>,
    extras: Record<string, unknown> = {},
): BuiltOptions {
    const options: Record<string, unknown> = {...extras};
    for (const decl of visibleOptions(decls, values)) {
        const raw = values[decl.name];
        if (decl.type === "bool") {
            options[decl.name] = raw === true;
            continue;
        }
        const text = raw === undefined || raw === null ? "" : String(raw).trim();
        if (!text) {
            if (decl.required) return {error: `${decl.title || decl.name} is required`};
            continue;
        }
        if (decl.type === "int" || decl.type === "float") {
            const n = Number(text);
            if (!Number.isFinite(n)) return {error: `${decl.title || decl.name} must be a number`};
            if (decl.type === "int" && !Number.isInteger(n)) {
                return {error: `${decl.title || decl.name} must be a whole number`};
            }
            options[decl.name] = n;
            continue;
        }
        if (decl.type === "string_list") {
            const items = text
                .split(",")
                .map((v) => v.trim())
                .filter(Boolean);
            if (items.length) options[decl.name] = items;
            continue;
        }
        options[decl.name] = text;
    }
    return {options};
}

/** Read a plugin spec's declared options, tolerating a spec that has none.
 *
 * A spec is advertised BY A WORKER, so the declaration can be absent (an older
 * build) or malformed (a worker mid-upgrade). Both have to degrade to "no
 * declaration, use the raw document" rather than break the panel — the form is
 * an affordance, and the API accepts the document either way. */
/** A spec key holding a list of strings, or undefined. */
function specStringList(spec: unknown, key: string): string[] | undefined {
    const raw = (spec as Record<string, unknown> | null)?.[key];
    if (!Array.isArray(raw)) return undefined;
    const items = raw.filter((v): v is string => typeof v === "string" && v.length > 0);
    return items.length ? items : undefined;
}

export function declaredOptions(spec: unknown): PluginJobOption[] {
    const raw = (spec as {job_options?: unknown} | null)?.job_options;
    if (!Array.isArray(raw)) return [];
    const out: PluginJobOption[] = [];
    for (const item of raw) {
        if (!item || typeof item !== "object") continue;
        const decl = item as PluginJobOption;
        if (typeof decl.name !== "string" || !decl.name) continue;
        if (!["bool", "string", "int", "float", "enum", "string_list"].includes(decl.type)) continue;
        const resolved = decl.enum_from ? {...decl, enum: specStringList(spec, decl.enum_from)} : decl;
        // An enum with no choices cannot be rendered as one, and rendering it as
        // free text would accept values the plugin will reject. This is also where
        // an `enum_from` naming a key the spec does not carry drops out.
        if (resolved.type === "enum" && !(Array.isArray(resolved.enum) && resolved.enum.length)) continue;
        out.push(resolved);
    }
    return out;
}
