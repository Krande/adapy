import React from "react";
import type {RowRendererProps} from "react-arborist";

/**
 * The Outliner's row wrapper, replacing react-arborist's DefaultRow for one reason:
 * multi-select on Windows and Linux.
 *
 * DefaultRow binds `node.handleClick`, which tests `e.metaKey` alone — the Command key.
 * On every other platform that key is never pressed, so Ctrl+click fell through to the
 * plain-click branch and simply replaced the selection. Multi-select in this tree has
 * therefore never worked outside macOS, quietly, in a way that looks like the feature was
 * not built rather than like a bug.
 *
 * Everything else is DefaultRow verbatim, including the focus-stopPropagation, so the
 * behaviour this changes is exactly the modifier test and nothing else.
 */
export function OutlinerRow<T>({node, attrs, innerRef, children}: RowRendererProps<T>) {
    return (
        <div
            {...attrs}
            ref={innerRef}
            onFocus={(e) => e.stopPropagation()}
            onClick={(e) => {
                const multiDisabled = node.tree.props.disableMultiSelection;
                // Cmd on macOS, Ctrl everywhere else. Accepting both rather than branching
                // on platform: a Mac user with an external PC keyboard reaches for Ctrl,
                // and there is no case where accepting the other one is wrong.
                if ((e.metaKey || e.ctrlKey) && !multiDisabled) {
                    if (node.isSelected) node.deselect();
                    else node.selectMulti();
                } else if (e.shiftKey && !multiDisabled) {
                    node.selectContiguous();
                } else {
                    node.select();
                    node.activate();
                }
            }}
        >
            {children}
        </div>
    );
}
