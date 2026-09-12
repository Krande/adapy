/**
 * The panel's pinned FOOTER.
 *
 * Owns: Commit, the Compile split-button and its LOD menu, and the revision
 * readout. An explicit Compile is always clickable on an active model: the
 * server is authoritative on cache-vs-rebuild.
 */

import React from "react";

import {PositionedMenu} from "@/components/common/PositionedMenu";
import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {btn, btnGray, isReadOnly} from "./chrome";

export const PanelFooter: React.FC<{
  /** Re-list the local-disk models after a save writes a new one. */
  onSaved: () => void;
}> = ({onSaved}) => {
  const s = useCellBuilderStore();
  const readOnly = isReadOnly(s);
  const compileState = s.compileJob;
  const compileBusy =
    compileState != null &&
        (compileState.status === "queued" || compileState.status === "running");
  const compileCaretRef = React.useRef<HTMLButtonElement>(null);
  const [compileMenuOpen, setCompileMenuOpen] = React.useState(false);
  return (
    <div className="flex items-center gap-2 px-2.5 py-2 border-t border-gray-600/50">
      <span className="inline-flex">
        <button
          className={btn + " rounded-r-none flex items-center gap-1.5"}
          // Always clickable on an active model: an explicit Compile re-consults
          // the server, which is authoritative on cache-vs-rebuild. It rebuilds
          // when anything the last compile depended on has changed — the document
          // itself OR the equipment/system CATALOGS this model draws from (edited
          // in the catalog window, so the doc stays "clean" and needsPreviewCompile
          // can't see it) — and returns the cached result cheaply when nothing has.
          disabled={compileBusy || readOnly || !capabilities.procedural.supports("previewModel")}
          onClick={() => void s.compilePreviewSelected()}
          title={
            readOnly
              ? "This model is already the compiled result in the scene — recompiling needs an editable model to compile from."
              : !capabilities.procedural.supports("previewModel")
                ? "Server-side compile is not available over this transport yet — use \"Compile in browser (WASM)\" from the ▾ menu instead."
                : "Compile a preview of the current model (⇧↵) at the selected level(s) of detail. Rebuilds when the model — or the equipment/system catalog it uses — changed since the last compile; serves the cached result otherwise. Nothing is saved."
          }
        >
          {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
          <kbd className="text-[10px] font-semibold bg-white/20 border border-white/25 rounded px-1">
            ⇧↵
          </kbd>
        </button>
        <button
          ref={compileCaretRef}
          className={btn + " rounded-l-none border-l border-white/25 px-1.5"}
          disabled={compileBusy || readOnly}
          title="More compile options"
          onClick={() => setCompileMenuOpen((v) => !v)}
        >
          ▾
        </button>
      </span>
      {compileMenuOpen && (
        <PositionedMenu
          anchor={{
            kind: "rect",
            getRect: () => compileCaretRef.current?.getBoundingClientRect(),
          }}
          ignoreOutsideRef={compileCaretRef}
          onClose={() => setCompileMenuOpen(false)}
          items={[
            {
              key: "recompile",
              label: "Recompile preview (force)",
              disabled: !capabilities.procedural.supports("previewModel"),
              title: !capabilities.procedural.supports("previewModel")
                ? "Server-side compile is not available over this transport yet"
                : "Rebuild the preview even if this doc is cached — use after a compiler/engine change when the document itself hasn't changed",
              onClick: () => void s.compilePreviewSelected(true),
            },
            {
              key: "browser",
              label: "Compile in browser (WASM)",
              title:
                "Compile the current (uncommitted) model in your browser via WebAssembly — no server round-trip. Catalog/CAD equipment falls back to built-in archetypes.",
              onClick: () => void s.compileInBrowser(),
            },
          ]}
        />
      )}
      <button
        className={btnGray}
        disabled={readOnly || !s.dirty || s.committing}
        onClick={() => {
          // The local-disk browser's listing (mtime/hash/size) goes stale the moment a commit
          // writes a new revision to the same model_id -- refresh it so a reopened browser
          // shows what is actually on disk now, not the pre-commit snapshot.
          void s.commit().then((ok) => {
            if (ok) onSaved();
          });
        }}
        title={
          readOnly
            ? "This model was loaded from the scene, not opened from storage — there is nowhere to commit it back to."
            : "Commit the current state as a new revision. If you've previewed this exact model, the commit promotes that build — no recompile."
        }
      >
        {s.committing ? "Committing…" : "Commit"}
      </button>
      <span className="ml-auto text-gray-400 whitespace-nowrap">
        {s.active ? `r${s.active.revision}` : "read-only"}
      </span>
    </div>
  );
};
