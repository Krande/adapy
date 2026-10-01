// The Scene tree's row: react-arborist's own, with Ctrl as well as Cmd adding a row to the selection
// (or taking it out when it is already in).
//
// The library reads `metaKey` alone for that (`NodeApi.handleClick`) -- the Cmd key on a Mac, and
// on Windows the Windows key, which a click never carries. So a Ctrl-click there REPLACED the
// selection: the one modifier a Windows user reaches for did the one thing it should not. Shift
// (a contiguous range) and a plain click are the library's, unchanged.

import React from "react";
import type { NodeApi, RowRendererProps } from "react-arborist";

import type { TreeNodeData } from "./CustomNode";

/** What a click on `node` does to the selection: Ctrl or Cmd toggles the row in or out of it;
 *  anything else is the library's own handling. */
export function clickRow(node: NodeApi<TreeNodeData>, e: React.MouseEvent): void {
  if ((e.ctrlKey || e.metaKey) && !node.tree.props.disableMultiSelection) {
    if (node.isSelected) node.deselect();
    else node.selectMulti();
    return;
  }
  node.handleClick(e);
}

const SceneTreeRow = ({ node, attrs, innerRef, children }: RowRendererProps<TreeNodeData>) => (
  <div {...attrs} ref={innerRef} onFocus={(e) => e.stopPropagation()} onClick={(e) => clickRow(node, e)}>
    {children}
  </div>
);

export default SceneTreeRow;
