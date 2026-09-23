// Pick the subtree a clash check is scoped to, from the model's own hierarchy.
//
// `ClashOptions.root` is the NAME OF A PART in the source model, and a name the source does not
// carry is refused by the check ("no part named 'x' in this model to scope the check to"). Typing
// it is therefore a guess that fails a job later; the hierarchy the viewer already loaded holds
// the exact set of admissible answers, so this offers that instead -- the same tree the Tree View
// panel renders (`useTreeViewStore.treeData`), narrowed to the nodes that HAVE children, since a
// leaf is a single beam or plate and scoping a joint search to one member finds nothing.
//
// The list is rendered in a PORTAL, positioned over the canvas. The scene panel it opens from is a
// narrow scrolling box, so an in-flow dropdown is clipped to a few rows of a hierarchy that can be
// deep -- the one part of this control that has to be readable.

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useTreeViewStore } from "@/state/treeViewStore";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";

export interface RootChoice {
  readonly name: string;
  readonly depth: number;
}

/** Flatten a loaded hierarchy into the choices a scope picker may offer: named nodes with
 *  children, de-duplicated by name (a name is what the check matches on, so two nodes sharing one
 *  would be the same answer twice), depth-ordered for indenting. Exported for its test. */
export function rootChoices(root: TreeNodeData | null, limit = 500): readonly RootChoice[] {
  if (!root) return [];
  const out: RootChoice[] = [];
  const seen = new Set<string>();
  const walk = (node: TreeNodeData, depth: number) => {
    if (out.length >= limit) return;
    const children = Array.isArray(node.children) ? node.children : [];
    if (children.length > 0 && node.name && !seen.has(node.name)) {
      seen.add(node.name);
      out.push({ name: node.name, depth });
    }
    for (const child of children) walk(child, depth + 1);
  };
  walk(root, 0);
  return out;
}

const ClashRootPicker: React.FC<{ value: string | null; onChange: (root: string | null) => void }> = ({
  value,
  onChange,
}) => {
  const treeData = useTreeViewStore((s) => s.treeData);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const anchorRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const choices = useMemo(() => rootChoices(treeData), [treeData]);
  const shown = useMemo(
    () => (query ? choices.filter((c) => c.name.toLowerCase().includes(query.toLowerCase())) : choices),
    [choices, query],
  );

  // Anchored to the button's viewport rect, remeasured on open: `position: fixed` is what lets the
  // list cover the canvas instead of being clipped by the panel's own scroll box.
  useLayoutEffect(() => {
    if (!open) return;
    const anchor = anchorRef.current;
    if (!anchor) return;
    const box = anchor.getBoundingClientRect();
    setRect({ top: box.bottom + 2, left: box.left, width: Math.max(box.width, 220) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (popoverRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    // `true` (capture): the canvas swallows pointer events for orbiting, so a bubble-phase listener
    // would never see a click that lands on the model.
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const list = (
    <div
      ref={popoverRef}
      className="fixed z-[1000] max-h-72 overflow-auto rounded-sm border border-gray-600 bg-gray-800 shadow-lg"
      style={{ top: rect?.top ?? 0, left: rect?.left ?? 0, width: rect?.width ?? 220 }}
    >
      {choices.length === 0 ? (
        <div className="px-2 py-1 text-[11px] text-gray-400">
          No hierarchy loaded — the whole model is the only scope available.
        </div>
      ) : (
        <>
          <input
            className="w-full bg-gray-700 text-white px-1 py-0.5 text-[11px] sticky top-0"
            placeholder="filter…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button
            type="button"
            className={`block w-full text-left px-2 py-0.5 text-[11px] hover:bg-gray-700 ${
              value === null ? "text-blue-300" : "text-gray-200"
            }`}
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
          >
            whole model
          </button>
          {shown.map((choice) => (
            <button
              key={choice.name}
              type="button"
              className={`block w-full text-left px-2 py-0.5 text-[11px] truncate hover:bg-gray-700 ${
                value === choice.name ? "text-blue-300" : "text-gray-200"
              }`}
              style={{ paddingLeft: `${0.5 + choice.depth * 0.6}rem` }}
              title={choice.name}
              onClick={() => {
                onChange(choice.name);
                setOpen(false);
              }}
            >
              {choice.name}
            </button>
          ))}
        </>
      )}
    </div>
  );

  return (
    <div className="flex items-center gap-1 text-[11px] text-gray-300 flex-1 min-w-0">
      root
      <button
        ref={anchorRef}
        type="button"
        className="flex-1 min-w-0 truncate text-left bg-gray-600 text-white rounded-sm px-1 py-0.5 hover:bg-gray-500"
        onClick={() => setOpen((v) => !v)}
        title="Scope the check to one part of the model hierarchy"
      >
        {value ?? "whole model"}
      </button>
      {value && (
        <button type="button" className="text-gray-400 hover:text-white" title="Clear" onClick={() => onChange(null)}>
          ×
        </button>
      )}
      {open && typeof document !== "undefined" && createPortal(list, document.body)}
    </div>
  );
};

export default ClashRootPicker;
