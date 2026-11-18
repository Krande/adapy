// Tree rows as the viewport's object references. Light (types only), so the viewport's commands and keys
// can use it without loading the scene's mesh helpers.
import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import type {ObjectRef} from "@/viewport/api/viewportApi";

/** The objects under `rows` (each row's whole subtree), as the viewport knows them. A row nested
 *  under another selected row is counted once. */
export function refsUnder(rows: readonly TreeNodeData[]): ObjectRef[] {
    const out: ObjectRef[] = [];
    const seen = new Set<string>();
    const stack = [...rows];
    while (stack.length) {
        const n = stack.pop()!;
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        for (const c of n.children ?? []) stack.push(c);
        if (n.model_key && n.rangeId != null) out.push({model: n.model_key, id: String(n.rangeId), node: n.node_name});
    }
    return out;
}
