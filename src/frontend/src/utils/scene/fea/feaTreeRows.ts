// The loaded FEA result's sets and model properties as Scene tree rows, and the
// arithmetic of isolating them.
//
// Pure: string and array work over the manifest's sets, its property fields and
// a membership map (which element carries which property value), so it is tested
// without a scene, a store or a browser. The store is `@/state/feaIsolationStore`,
// the scene side `./feaIsolation`, the rows' provider `./feaTree`.
//
// Row ids are prefixed (`fea:`), so a row is told from a model's own row by its id
// alone, and an FEA set whose name matches a mesh name never behaves like it.
//
//   fea:sets                      "Sets"            container
//     fea:sets:elements           "Element sets"    container (only with node sets too)
//       fea:set:e:<name>          one element set   isolatable
//     fea:sets:nodes              "Node sets"       container (only with element sets too)
//       fea:set:n:<name>          one node set      informational: nodes carry no faces
//   fea:props                     "Properties"      container
//     fea:prop:<field>            one property      container (its values once read)
//       fea:propval:<field>:<v>   one value         isolatable

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import type {FeaManifestField} from "@/services/api/fea";

import {drawRangeIdFor, isElementSet, type FeaSet} from "./feaSets";

/** A set as the manifest carries it (inline `groups`, or the `groups_url` sidecar). */
export type FeaSetInfo = FeaSet & {role?: string};

/** Draw-range id (`E<label>`) -> the property value that element carries. */
export type PropertyMembership = ReadonlyMap<string, number>;

/** How a property's membership stands: read, being read, or unreadable. */
export type MembershipStatus = "loading" | "failed";

export const FEA_SETS_ROOT_ID = "fea:sets";
export const FEA_ELEMENT_SETS_ID = "fea:sets:elements";
export const FEA_NODE_SETS_ID = "fea:sets:nodes";
export const FEA_PROPERTIES_ROOT_ID = "fea:props";

const ELEMENT_SET = "fea:set:e:";
const NODE_SET = "fea:set:n:";
const PROPERTY = "fea:prop:";
const VALUE = "fea:propval:";

/** Row id of element set `name`. */
export const elementSetRowId = (name: string): string => `${ELEMENT_SET}${name}`;
/** Row id of node set `name`. */
export const nodeSetRowId = (name: string): string => `${NODE_SET}${name}`;
/** Row id of property field `field`'s container. */
export const propertyRowId = (field: string): string => `${PROPERTY}${field}`;
/** Row id of value `value` of property field `field`. */
export const propertyValueRowId = (field: string, value: number): string => `${VALUE}${field}:${value}`;

/** Is `id` one of these rows (any kind)? */
export const isFeaTreeRowId = (id: string): boolean => id.startsWith("fea:");

/** The element set behind a row id, or null. */
export const elementSetFromRowId = (id: string): string | null => (id.startsWith(ELEMENT_SET) ? id.slice(ELEMENT_SET.length) : null);

/** The node set behind a row id, or null. */
export const nodeSetFromRowId = (id: string): string | null => (id.startsWith(NODE_SET) ? id.slice(NODE_SET.length) : null);

/** The (field, value) behind a property value's row id, or null. Field names may hold dots
 *  and colons, the value never a colon, so the LAST colon separates them. */
export function propertyValueFromRowId(id: string): {field: string; value: number} | null {
    if (!id.startsWith(VALUE)) return null;
    const body = id.slice(VALUE.length);
    const cut = body.lastIndexOf(":");
    if (cut <= 0) return null;
    const raw = body.slice(cut + 1);
    const value = Number(raw);
    if (raw === "" || !Number.isFinite(value)) return null;
    return {field: body.slice(0, cut), value};
}

/** Can the row be isolated -- an element set or a property value? */
export const isIsolatableRowId = (id: string): boolean => elementSetFromRowId(id) !== null || propertyValueFromRowId(id) !== null;

/** The model-property fields of a manifest that have element buckets to read, in manifest order. */
export function propertyFieldsOf(fields: readonly FeaManifestField[] | null | undefined): FeaManifestField[] {
    return (fields ?? []).filter((f) => f.category === "property" && (f.per_type?.length ?? 0) > 0);
}

/** What a property is called in the tree: the last name of its group path, else the field
 *  name without its namespace, in sentence case ("props.thickness" -> "Thickness"). */
export function propertyLabel(field: Pick<FeaManifestField, "name_canonical" | "group_path">): string {
    const raw = field.group_path?.[field.group_path.length - 1] ?? field.name_canonical.replace(/^props\./, "");
    const words = raw.replace(/[_\s]+/g, " ").trim().toLowerCase();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : field.name_canonical;
}

/** What one value is called: the field's own name for it (a category), else the number with
 *  its unit (a measurement). */
export function propertyValueLabel(value: number, field: Pick<FeaManifestField, "value_labels" | "unit">): string {
    const named = field.value_labels?.[String(value)];
    if (named) return named;
    const shown = Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(6)));
    return field.unit ? `${shown} ${field.unit}` : shown;
}

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);

/** The inputs of the tree rows. */
export interface FeaTreeInputs {
    sets: readonly FeaSetInfo[];
    propertyFields: readonly FeaManifestField[];
    membership: ReadonlyMap<string, PropertyMembership>;
    /** Fields whose membership is not read (yet, or at all). */
    membershipStatus: ReadonlyMap<string, MembershipStatus>;
    /** The isolated rows' ids: drawn marked. */
    isolated: ReadonlySet<string>;
}

const container = (id: string, name: string, children: TreeNodeData[], meta?: string): TreeNodeData => ({id, name, children, meta: meta ?? fmt(children.length)});

function setRows(sets: readonly FeaSetInfo[], isolated: ReadonlySet<string>): {elements: TreeNodeData[]; nodes: TreeNodeData[]} {
    // One row per name and kind: sets repeat names freely across kinds, and a row id may
    // appear once in the tree.
    const elementCounts = new Map<string, number>();
    const nodeCounts = new Map<string, number>();
    for (const s of sets) {
        const into = isElementSet(s) ? elementCounts : nodeCounts;
        into.set(s.name, (into.get(s.name) ?? 0) + s.members.length);
    }
    const elements = [...elementCounts].map(([name, n]): TreeNodeData => {
        const id = elementSetRowId(name);
        return {id, name, children: [], meta: `${fmt(n)} el`, marked: isolated.has(id) || undefined, title: `Element set: ${fmt(n)} elements. Select to isolate it.`};
    });
    const nodes = [...nodeCounts].map(([name, n]): TreeNodeData => ({
        id: nodeSetRowId(name),
        name,
        children: [],
        meta: `${fmt(n)} n`,
        informational: true,
        title: `Node set: ${fmt(n)} nodes. Nodes carry no faces, so a node set cannot isolate elements.`,
    }));
    return {elements, nodes};
}

/** The "Sets" root, or null when the result carries no sets. Element sets and node sets in
 *  branches of their own when there are both. */
export function buildSetsRoot(sets: readonly FeaSetInfo[], isolated: ReadonlySet<string> = new Set()): TreeNodeData | null {
    if (sets.length === 0) return null;
    const {elements, nodes} = setRows(sets, isolated);
    if (elements.length && nodes.length) {
        return container(FEA_SETS_ROOT_ID, "Sets", [
            container(FEA_ELEMENT_SETS_ID, "Element sets", elements),
            {...container(FEA_NODE_SETS_ID, "Node sets", nodes), informational: true, title: "Nodes carry no faces: node sets are listed and counted, not isolated"},
        ], fmt(elements.length + nodes.length));
    }
    return container(FEA_SETS_ROOT_ID, "Sets", elements.length ? elements : nodes);
}

/** One property's row: its values with the number of elements carrying each, once its
 *  membership is read; "reading..." / "unavailable" until then. Values sorted by name for a
 *  category, numerically for a measurement. */
export function buildPropertyRow(
    field: FeaManifestField,
    membership: PropertyMembership | undefined,
    status: MembershipStatus | undefined,
    isolated: ReadonlySet<string> = new Set(),
): TreeNodeData {
    const id = propertyRowId(field.name_canonical);
    const name = propertyLabel(field);
    if (!membership) {
        const failed = status === "failed";
        return {
            id,
            name,
            children: [],
            meta: failed ? "unavailable" : "reading…",
            informational: true,
            title: failed ? "Which elements carry which value could not be read" : "Reading which elements carry which value",
        };
    }
    const counts = new Map<number, number>();
    for (const v of membership.values()) counts.set(v, (counts.get(v) ?? 0) + 1);
    const categorical = !!field.value_labels && Object.keys(field.value_labels).length > 0;
    const values = [...counts].map(([value, n]) => ({value, n, label: propertyValueLabel(value, field)}));
    values.sort((a, b) => (categorical ? a.label.localeCompare(b.label, undefined, {numeric: true}) : a.value - b.value));
    const children = values.map(({value, n, label}): TreeNodeData => {
        const rowId = propertyValueRowId(field.name_canonical, value);
        return {id: rowId, name: label, children: [], meta: `${fmt(n)} el`, marked: isolated.has(rowId) || undefined, title: `${name} ${label}: ${fmt(n)} elements. Select to isolate them.`};
    });
    return {id, name, children, meta: fmt(children.length)};
}

/** The rows after the models: "Sets" and "Properties", each only when there is something in it. */
export function buildFeaTreeRoots(inputs: FeaTreeInputs): TreeNodeData[] {
    const out: TreeNodeData[] = [];
    const sets = buildSetsRoot(inputs.sets, inputs.isolated);
    if (sets) out.push(sets);
    if (inputs.propertyFields.length) {
        const rows = inputs.propertyFields.map((f) =>
            buildPropertyRow(f, inputs.membership.get(f.name_canonical), inputs.membershipStatus.get(f.name_canonical), inputs.isolated),
        );
        out.push(container(FEA_PROPERTIES_ROOT_ID, "Properties", rows));
    }
    return out;
}

/** The draw ranges an isolation of `rowIds` keeps: the union of the element sets' members
 *  and the property values' elements, de-duplicated, in order. Node sets and unknown ids
 *  name nothing. */
export function isolatedMembers(
    rowIds: Iterable<string>,
    sets: readonly FeaSetInfo[],
    membership: ReadonlyMap<string, PropertyMembership>,
): string[] {
    const setNames = new Set<string>();
    const values = new Map<string, Set<number>>();
    for (const id of rowIds) {
        const s = elementSetFromRowId(id);
        if (s !== null) {
            setNames.add(s);
            continue;
        }
        const v = propertyValueFromRowId(id);
        if (v) {
            const into = values.get(v.field) ?? new Set<number>();
            into.add(v.value);
            values.set(v.field, into);
        }
    }
    const seen = new Set<string>();
    const out: string[] = [];
    const add = (m: string) => {
        if (seen.has(m)) return;
        seen.add(m);
        out.push(m);
    };
    for (const s of sets) {
        if (!setNames.has(s.name) || !isElementSet(s)) continue;
        for (const raw of s.members) {
            const m = drawRangeIdFor(raw);
            if (m !== null) add(m);
        }
    }
    for (const [field, wanted] of values) {
        const map = membership.get(field);
        if (!map) continue;
        for (const [rangeId, own] of map) if (wanted.has(own)) add(rangeId);
    }
    return out;
}

/** `current` changed by `ids` in `mode`: replaced by them, with them added, or with them taken
 *  out. Only isolatable ids are kept; order is first seen. */
export function combineIsolation(current: readonly string[], ids: readonly string[], mode: "replace" | "add" | "remove"): string[] {
    const wanted = ids.filter(isIsolatableRowId);
    if (mode === "replace") return [...new Set(wanted)];
    if (mode === "add") return [...new Set([...current, ...wanted])];
    const drop = new Set(wanted);
    return current.filter((id) => !drop.has(id));
}

/** Element sets whose name holds `text` (case ignored, blank: none), as row ids. */
export function setRowsMatching(sets: readonly FeaSetInfo[], text: string): string[] {
    const q = text.trim().toLowerCase();
    if (!q) return [];
    const out = new Set<string>();
    for (const s of sets) if (isElementSet(s) && s.name.toLowerCase().includes(q)) out.add(elementSetRowId(s.name));
    return [...out];
}

/** Why the "rest as wireframe" toggle cannot act, or null when it can.
 *
 *  It draws the element edges of what an isolation hides, so it needs a result with element
 *  edges; and an isolation of node sets alone hides nothing, so there is no rest to draw. With
 *  nothing isolated the toggle still records the choice for the next isolation. */
export function contextWireframeReason(args: {
    loaded: boolean;
    hasEdges: boolean;
    isolated: readonly string[];
    /** The draw ranges the isolation keeps. */
    members: number;
}): string | null {
    if (!args.loaded) return "No FEA result is loaded";
    if (!args.hasEdges) return "This result carries no element edges to draw the rest with";
    if (args.isolated.length > 0 && args.members === 0) return "The isolated rows name no elements, so nothing is hidden";
    return null;
}

/** Why an isolation command cannot act, or null. */
export function isolationReason(args: {loaded: boolean; sets: number; properties: number}): string | null {
    if (!args.loaded) return "No FEA result is loaded";
    if (args.sets === 0 && args.properties === 0) return "The loaded result carries no sets or properties";
    return null;
}

/** Set membership of every element: draw-range id -> the element sets naming it, in manifest
 *  order. One pass over the members, for the Selected Object panel's "in sets" line. */
export function setsByElement(sets: readonly FeaSetInfo[]): Map<string, string[]> {
    const out = new Map<string, string[]>();
    for (const s of sets) {
        if (!isElementSet(s)) continue;
        for (const raw of s.members) {
            const m = drawRangeIdFor(raw);
            if (m === null) continue;
            const names = out.get(m);
            if (!names) out.set(m, [s.name]);
            else if (names[names.length - 1] !== s.name) names.push(s.name);
        }
    }
    return out;
}
