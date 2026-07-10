import React, {useCallback} from "react";
import {
    SerializerSelection,
    serializerSchemaFor,
    tessellatorsFor,
    normalizeSelection,
} from "@/services/conversion/serializerMatrix";

// Two dependent dropdowns — Serializer × Tessellator — rendered entirely from
// the backend-advertised matrix (labels + enum_by), never from a hardcoded
// list. Changing the serializer repopulates the tessellator to the first
// kernel that serializer allows. Shared by the gallery reconvert tool and the
// /convert page ConversionRow so the two stay in lockstep.
//
// Emits a full {serializer, tessellator} pair on every change. The parent
// decides routing (server vs in-browser) via serializerMatrix.normalizeSelection.
export const SerializerTessellatorSelect: React.FC<{
    ext: string;
    target: string;
    value: SerializerSelection;
    onChange: (v: SerializerSelection) => void;
    disabled?: boolean;
    compact?: boolean;
}> = ({ext, target, value, onChange, disabled = false, compact = false}) => {
    const schema = serializerSchemaFor(ext, target);

    const onSerializer = useCallback(
        (serializer: string) => {
            if (!schema) return;
            // Reselect the tessellator to the first kernel this serializer allows.
            const allowed = tessellatorsFor(schema, serializer);
            onChange({serializer, tessellator: allowed[0]});
        },
        [schema, onChange],
    );

    const onTessellator = useCallback(
        (tessellator: string) => {
            if (!schema) return;
            const {serializer} = normalizeSelection(schema, value);
            onChange({serializer, tessellator});
        },
        [schema, value, onChange],
    );

    if (!schema) return null;

    const {serializer, tessellator} = normalizeSelection(schema, value);
    const tessTokens = tessellatorsFor(schema, serializer);
    const serLabels = schema.serializer.labels ?? {};
    const tessLabels = schema.tessellator.labels ?? {};
    // Axis display names come from the backend (single source): "Tessellator" for mesh targets,
    // "Writer" for B-rep targets. Fall back to the option name.
    const serTitle = (schema.serializer.title ?? "serializer").toLowerCase();
    const tessTitle = (schema.tessellator.title ?? schema.tessellator.name).toLowerCase();

    const selCls = compact
        ? "rounded-sm border border-gray-600 bg-gray-800 px-1 py-0.5 text-[11px] text-gray-100"
        : "bg-gray-900 border border-gray-600 rounded-sm px-1 py-0.5 text-xs text-gray-100";
    const lblCls = compact
        ? "flex items-center gap-1 text-[11px] text-gray-300"
        : "inline-flex items-center gap-1 text-xs text-gray-300";

    return (
        <>
            <label className={lblCls} title={schema.serializer.description || serTitle}>
                <span>{serTitle}:</span>
                <select
                    aria-label="Serializer"
                    className={selCls}
                    value={serializer}
                    onChange={(e) => onSerializer(e.target.value)}
                    disabled={disabled}
                >
                    {(schema.serializer.enum ?? []).map((v) => (
                        <option key={v} value={v}>{serLabels[v] ?? v}</option>
                    ))}
                </select>
            </label>
            <label className={lblCls} title={schema.tessellator.description || tessTitle}>
                <span>{tessTitle}:</span>
                <select
                    aria-label="Tessellator"
                    className={selCls}
                    value={tessellator}
                    onChange={(e) => onTessellator(e.target.value)}
                    // Pinned kernel (cpp): a single choice — show it, but there's nothing to pick.
                    disabled={disabled || tessTokens.length <= 1}
                >
                    {tessTokens.map((v) => (
                        <option key={v} value={v}>{tessLabels[v] ?? v}</option>
                    ))}
                </select>
            </label>
        </>
    );
};
