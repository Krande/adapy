// A picked FEA element, as the flat Properties layout draws it: an ELEMENT badge and its number,
// then its element type, instance and nodes, its section (name and dimensions), its plate thickness
// and its material (name, E, ν, ρ, yield). The data is core's own for an FEA pick
// (`useFeaElementData`: the bake's element-property sidecar, the manifest's section and material
// tables and the element buckets' nodes), the same the classic "Properties" rows read. Drawn while
// the panel is flat (`ObjectInfoBodyProps.flat`); the classic panel keeps those rows instead.

import React from "react";
import { Field, PropertyItemLayout, type PropertyItemGroup } from "./cellBuilderSelection/PropertyItemLayout";
import { formatThickness, materialFields, sectionFields, useFeaElementData } from "./ObjectMetadataPanel";

const pairs = (rows: [string, string][]) =>
    rows.map(([label, text]) => (
        <Field key={label} label={label}>
            <span className="text-gray-100">{text}</span>
        </Field>
    ));

/** The element's property groups (pure, for its tests). */
export function feaElementGroups(data: NonNullable<ReturnType<typeof useFeaElementData>>): PropertyItemGroup[] {
    const groups: PropertyItemGroup[] = [
        {
            title: "Element",
            fields: (
                <>
                    <Field label="Type">
                        <span className="text-gray-100">
                            {data.elemType ?? "—"}
                            {data.typeName ? ` (${data.typeName})` : ""}
                        </span>
                    </Field>
                    {data.instance && (
                        <Field label="Instance">
                            <span className="text-gray-100">{data.instance}</span>
                        </Field>
                    )}
                    {data.nodes.length > 0 && (
                        <Field label="Nodes" inline>
                            <span className="break-all text-gray-100">{data.nodes.join(", ")}</span>
                        </Field>
                    )}
                </>
            ),
        },
    ];
    if (data.section) {
        groups.push({
            title: "Section",
            fields: (
                <>
                    <Field label="Name">
                        <span className="text-gray-100">{data.section.name ?? "—"}</span>
                    </Field>
                    {pairs(sectionFields(data.section))}
                </>
            ),
        });
    }
    if (data.thickness !== null) {
        groups.push({
            title: "Plate",
            fields: (
                <Field label="Thickness">
                    <span className="text-gray-100">{formatThickness(data.thickness)}</span>
                </Field>
            ),
        });
    }
    if (data.material) {
        groups.push({
            title: "Material",
            fields: (
                <>
                    <Field label="Name">
                        <span className="text-gray-100">{data.material.name ?? "—"}</span>
                    </Field>
                    {pairs(materialFields(data.material))}
                </>
            ),
        });
    }
    return groups;
}

/** The picked element (`name`, its draw range "E12"), or nothing for any other pick. */
export const FeaElementSection: React.FC<{ name: string | null }> = ({ name }) => {
    const data = useFeaElementData(name);
    if (!data) return null;
    return (
        <PropertyItemLayout
            badge="Element"
            name={String(data.label)}
            groups={feaElementGroups(data)}
            dataAttrs={{ "data-fea-element": String(data.label) }}
        />
    );
};

export default FeaElementSection;
