import {NodeApi} from "react-arborist";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";

import {selectRows} from "./treeSelectionActions";

/** A selection made in the scene tree widget (its nodes): their rows selected
 *  in 3D (`selectRows`: each row's subtree, one undo entry, the object info
 *  naming the first row). */
export async function handleTreeSelectionChange(ids: NodeApi[]) {
    await selectRows(ids.map((n) => n.data as TreeNodeData));
}
