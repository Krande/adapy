import React from "react";

import {
    PluginJobOption,
    PluginOptionValue,
    choicesFor,
    visibleOptions,
} from "./pluginOptionFields";

// Renders a plugin's declared job options as controls.
//
// The plugin owns the vocabulary; this file owns none of it. Every label, choice
// and default comes off the declaration, so a plugin gains an option by shipping
// a worker, with no change here and no core change either.

const INPUT = "bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100";

/** The label for an enum value: the plugin's own, or the wire token. */
function labelFor(decl: PluginJobOption, value: string): string {
    return decl.labels?.[value] ?? value;
}

const OptionWidget: React.FC<{
    decl: PluginJobOption;
    value: PluginOptionValue | undefined;
    values: Record<string, unknown>;
    onChange: (v: PluginOptionValue) => void;
}> = ({decl, value, values, onChange}) => {
    const label = decl.title || decl.name;
    const hint = decl.description || decl.name;

    if (decl.type === "bool") {
        return (
            <label className="text-xs text-gray-300 flex items-center gap-1 h-[30px] mt-auto" title={hint}>
                <input
                    type="checkbox"
                    checked={value === true}
                    onChange={(e) => onChange(e.target.checked)}
                    className="accent-blue-600"
                />
                <span>{label}</span>
            </label>
        );
    }

    if (decl.type === "enum") {
        const choices = choicesFor(decl, values);
        return (
            <label className="text-xs text-gray-300 flex flex-col gap-1" title={hint}>
                <span>{label}</span>
                <select
                    value={String(value ?? "")}
                    onChange={(e) => onChange(e.target.value)}
                    className={`${INPUT} w-48`}
                >
                    {/* An empty choice unless the option is required: leaving it
                        blank omits the key, so the plugin's own default applies. */}
                    {!decl.required && <option value="">(default)</option>}
                    {choices.map((v) => (
                        <option key={v} value={v}>
                            {labelFor(decl, v)}
                        </option>
                    ))}
                </select>
            </label>
        );
    }

    if (decl.type === "int" || decl.type === "float") {
        return (
            <label className="text-xs text-gray-300 flex flex-col gap-1" title={hint}>
                <span>{label}</span>
                <input
                    type="number"
                    step={decl.type === "int" ? 1 : "any"}
                    value={String(value ?? "")}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={decl.placeholder}
                    className={`${INPUT} w-28`}
                />
            </label>
        );
    }

    // string and string_list. A list is comma-separated text rather than a
    // repeater: these are short (a handful of user names, a couple of refs), and
    // a repeater costs a row of chrome per entry to save a comma.
    const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
    return (
        <label className="text-xs text-gray-300 flex flex-col gap-1" title={hint}>
            <span>
                {label}
                {decl.type === "string_list" && <span className="text-gray-500"> (comma-separated)</span>}
            </span>
            <input
                type="text"
                value={text}
                onChange={(e) => onChange(e.target.value)}
                placeholder={decl.placeholder}
                className={`${INPUT} ${decl.type === "string_list" ? "w-72" : "w-56"}`}
            />
        </label>
    );
};

/** The declared options that currently apply, as controls.
 *
 * Re-filtered on every render rather than on change, so choosing an action
 * immediately shows that action's options and hides the previous one's — the
 * values of the hidden ones are kept in state, because flipping between two
 * actions while comparing them should not clear what was typed.
 */
const PluginOptionsForm: React.FC<{
    decls: readonly PluginJobOption[];
    values: Record<string, PluginOptionValue>;
    onChange: (next: Record<string, PluginOptionValue>) => void;
}> = ({decls, values, onChange}) => {
    const shown = visibleOptions(decls, values);
    if (!shown.length) return null;
    return (
        <div className="flex flex-wrap items-end gap-2 w-full">
            {shown.map((decl) => (
                <OptionWidget
                    key={decl.name}
                    decl={decl}
                    value={values[decl.name]}
                    values={values}
                    onChange={(v) => onChange({...values, [decl.name]: v})}
                />
            ))}
        </div>
    );
};

export default PluginOptionsForm;
