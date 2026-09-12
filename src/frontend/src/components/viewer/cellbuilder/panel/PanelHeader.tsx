/**
 * The panel's pinned HEADER.
 *
 * Owns: the model identity row — name, revision or a read-only marker, the
 * unsaved dot, the local-disk model browser button, undo/redo and close. An
 * embedded document has no model id, name or revision (it came out of a GLB, not
 * a stored model), so the header names what it is instead of dereferencing a
 * session that is not there.
 */

import React from "react";

import {PositionedMenu} from "@/components/common/PositionedMenu";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import type {LocalModelBrowser} from "./useLocalModelBrowser";

export const PanelHeader: React.FC<{browser: LocalModelBrowser}> = ({browser}) => {
  const s = useCellBuilderStore();
  const btnRef = React.useRef<HTMLButtonElement>(null);
  return (
    <div className="shrink-0 flex items-center gap-2 px-2.5 py-2 border-b border-gray-600/50">
      {/* An embedded document has no model id, name or revision -- it came out
          of a GLB, not out of a stored model -- so the header names what it is
          instead of dereferencing a session that isn't there. */}
      <span
        className="font-semibold truncate"
        title={s.active?.modelId ?? "Loaded from the model in the scene"}
      >
        {s.active?.name ?? "Procedural model"}
      </span>
      {s.active ? (
        <span className="text-gray-400">r{s.active.revision}</span>
      ) : (
        <span className="text-gray-400 whitespace-nowrap">read-only</span>
      )}
      {s.dirty && (
        <span className="text-amber-400 whitespace-nowrap">● unsaved</span>
      )}
      {browser.supported && (
        <button
          ref={btnRef}
          className="ml-auto px-1.5 py-0.5 rounded-sm hover:bg-gray-500/40 whitespace-nowrap"
          title="Browse procedural models saved to local disk"
          onClick={() => {
            if (!browser.menuOpen) browser.refresh();
            browser.setMenuOpen((v) => !v);
          }}
        >
          Local models ▾
        </button>
      )}
      {browser.menuOpen && (
        <PositionedMenu
          anchor={{
            kind: "rect",
            getRect: () => btnRef.current?.getBoundingClientRect(),
          }}
          ignoreOutsideRef={btnRef}
          onClose={() => browser.setMenuOpen(false)}
          header={
            <span className="font-medium text-gray-200 flex items-center gap-2">
              Local models
              <button
                className="ml-auto text-gray-400 hover:text-white disabled:opacity-40"
                title="Refresh"
                disabled={browser.busy}
                onClick={(e) => {
                  e.stopPropagation();
                  browser.refresh();
                }}
              >
                ⟳
              </button>
            </span>
          }
          items={
            browser.error
              ? [{ key: "error", label: browser.error, disabled: true, onClick: () => {} }]
              : browser.busy && browser.entries.length === 0
                ? [{ key: "loading", label: "Loading…", disabled: true, onClick: () => {} }]
                : browser.entries.length
                  ? browser.entries.map((entry) => ({
                      key: entry.modelId,
                      label: entry.modelId,
                      title: `${new Date(entry.modifiedAt).toLocaleString()} — ${entry.sizeBytes} bytes`,
                      onClick: () => browser.open(entry),
                    }))
                  : [{ key: "empty", label: "No models saved yet", disabled: true, onClick: () => {} }]
          }
        />
      )}
      <button
        className={
          (browser.supported ? "" : "ml-auto ") +
          "px-1 rounded-sm hover:bg-gray-500/40 disabled:opacity-30"
        }
        title="Undo (Ctrl+Z)"
        disabled={s.past.length === 0}
        onClick={s.undo}
      >
        ↶
      </button>
      <button
        className="px-1 rounded-sm hover:bg-gray-500/40 disabled:opacity-30"
        title="Redo (Ctrl+Shift+Z)"
        disabled={s.future.length === 0}
        onClick={s.redo}
      >
        ↷
      </button>
      <button
        className="px-1 rounded-sm hover:bg-red-500/30"
        title="Close model"
        onClick={s.close}
      >
        ✕
      </button>
    </div>
  );
};
