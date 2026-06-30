import React, {useEffect, useRef, useState} from "react";
import {
    FileTreeNode,
    loadExpandedFolders,
    saveExpandedFolders,
} from "@/utils/storage/fileTree";
import FileTypeIcon from "../icons/FileTypeIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FolderOpenIcon from "../icons/FolderOpenIcon";
import ChevronRightIcon from "../icons/ChevronRightIcon";

// Generic, read-mostly folder-tree view shared by the admin Corpus tab's
// file overview and its "Copy from scope" modal. Storage stays flat on the
// server; folders are presentational (a key's "/" segments). The tree shape
// itself is built by the caller via ``buildFileTree`` from
// ``@/utils/storage/fileTree`` — this component only renders + manages
// collapse state, mirroring StorageBrowser's row look (indent per depth,
// chevron + folder glyph, file rows) without its drag/rename/scene machinery.

export interface FileTreeSelection {
    selected: ReadonlySet<string>;
    /** Toggle a batch of file keys on/off. A folder checkbox passes every
     * (enabled) descendant key — recursive select in one call. */
    onSelect: (keys: string[], select: boolean) => void;
}

interface FileTreeViewProps<T> {
    nodes: FileTreeNode<T>[];
    /** Stable identity for a file entry — the storage key. */
    getKey: (file: T) => string;
    /** Persist collapse state per-scope (same namespacing as StorageBrowser). */
    namespace: string;
    scope: string;
    /** Optional checkbox selection. A folder check toggles all descendant
     * file keys; partially-selected folders render an indeterminate box. */
    selection?: FileTreeSelection;
    /** Greyed, non-selectable files (e.g. already present in the corpus).
     * Excluded from folder-level select too. */
    isDisabled?: (file: T) => boolean;
    /** Right-aligned per-file slot for actions / labels (size, delete, …). */
    renderFileTail?: (file: T) => React.ReactNode;
}

// Every enabled descendant file key under a node — drives folder-level
// (recursive) selection and the all/some/none folder checkbox state.
function collectFileKeys<T>(
    node: FileTreeNode<T>,
    getKey: (file: T) => string,
    isDisabled?: (file: T) => boolean,
): string[] {
    if (node.kind === "file") {
        return isDisabled?.(node.file) ? [] : [getKey(node.file)];
    }
    const out: string[] = [];
    for (const child of node.children) {
        out.push(...collectFileKeys(child, getKey, isDisabled));
    }
    return out;
}

// Checkbox that can show the third (indeterminate) state — set
// imperatively since React doesn't expose ``indeterminate`` as a prop.
const TriCheckbox: React.FC<{
    checked: boolean;
    indeterminate?: boolean;
    disabled?: boolean;
    onChange: () => void;
}> = ({checked, indeterminate, disabled, onChange}) => {
    const ref = useRef<HTMLInputElement>(null);
    useEffect(() => {
        if (ref.current) ref.current.indeterminate = !!indeterminate && !checked;
    }, [indeterminate, checked]);
    return (
        <input
            ref={ref}
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={onChange}
            onClick={(e) => e.stopPropagation()}
        />
    );
};

export function FileTreeView<T>({
    nodes,
    getKey,
    namespace,
    scope,
    selection,
    isDisabled,
    renderFileTail,
}: FileTreeViewProps<T>): React.ReactElement {
    // Default fully collapsed; persisted per-scope so expand state survives
    // reloads but doesn't leak across scopes (matches StorageBrowser).
    const [expanded, setExpanded] = useState<Set<string>>(
        () => loadExpandedFolders(namespace, scope),
    );
    useEffect(() => {
        setExpanded(loadExpandedFolders(namespace, scope));
    }, [namespace, scope]);
    useEffect(() => {
        saveExpandedFolders(namespace, scope, expanded);
    }, [namespace, scope, expanded]);
    const toggleFolder = (path: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    const renderNode = (node: FileTreeNode<T>, depth: number): React.ReactNode => {
        const indentPx = depth * 12;
        if (node.kind === "file") {
            const key = getKey(node.file);
            const disabled = isDisabled?.(node.file) ?? false;
            const checked = selection ? selection.selected.has(key) : false;
            const onRowSelect = () => {
                if (!selection || disabled) return;
                selection.onSelect([key], !checked);
            };
            return (
                <li
                    key={`file:${key}`}
                    className={
                        "flex items-center gap-1.5 px-2 py-1 rounded select-none " +
                        (disabled
                            ? "opacity-50 "
                            : selection
                                ? "cursor-pointer hover:bg-gray-800/60 "
                                : "hover:bg-gray-800/60 ")
                    }
                    style={{paddingLeft: 8 + indentPx}}
                    onClick={onRowSelect}
                >
                    {selection && (
                        <input
                            type="checkbox"
                            className="shrink-0 cursor-pointer disabled:cursor-not-allowed"
                            checked={checked}
                            disabled={disabled}
                            onChange={onRowSelect}
                            onClick={(e) => e.stopPropagation()}
                        />
                    )}
                    <FileTypeIcon name={key}/>
                    <span
                        className="font-mono text-xs text-gray-200 flex-1 min-w-0 truncate"
                        title={key}
                    >
                        {node.displayName}
                    </span>
                    {renderFileTail && (
                        <span className="shrink-0" onClick={(e) => e.stopPropagation()}>
                            {renderFileTail(node.file)}
                        </span>
                    )}
                </li>
            );
        }
        const isOpen = expanded.has(node.path);
        const fileKeys = collectFileKeys(node, getKey, isDisabled);
        const total = fileKeys.length;
        const selCount = selection
            ? fileKeys.reduce((n, k) => (selection.selected.has(k) ? n + 1 : n), 0)
            : 0;
        const allSelected = total > 0 && selCount === total;
        const onFolderSelect = () => {
            if (!selection || total === 0) return;
            selection.onSelect(fileKeys, !allSelected);
        };
        return (
            <React.Fragment key={`folder:${node.path}`}>
                <li
                    className={
                        "flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer " +
                        "select-none hover:bg-gray-800/80"
                    }
                    style={{paddingLeft: 8 + indentPx}}
                    onClick={() => toggleFolder(node.path)}
                    role="button"
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? "Collapse" : "Expand"} folder ${node.name}`}
                >
                    {selection && (
                        <TriCheckbox
                            checked={allSelected}
                            indeterminate={selCount > 0}
                            disabled={total === 0}
                            onChange={onFolderSelect}
                        />
                    )}
                    <ChevronRightIcon
                        className={
                            "shrink-0 text-blue-400 transition-transform duration-150 " +
                            (isOpen ? "rotate-90" : "")
                        }
                    />
                    {isOpen ? (
                        <FolderOpenIcon className="shrink-0 text-blue-400"/>
                    ) : (
                        <FolderClosedIcon className="shrink-0 text-blue-400"/>
                    )}
                    <span className="text-xs flex-1 min-w-0 truncate font-semibold text-gray-200">
                        {node.name}/
                    </span>
                    <span className="text-[10px] text-gray-400 shrink-0">{total}</span>
                </li>
                {isOpen && node.children.map((c) => renderNode(c, depth + 1))}
            </React.Fragment>
        );
    };

    return (
        <ul className="flex flex-col divide-y divide-gray-700/60 text-xs">
            {nodes.map((n) => renderNode(n, 0))}
        </ul>
    );
}

export default FileTreeView;
