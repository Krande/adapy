import React from "react";
import { createPortal } from "react-dom";

import { useCellBuilderStore } from "@/state/cellBuilderStore";

// The cell menu's Rename…: a small field at the menu's place. Enter renames
// (one undo step; a blank or taken name is refused and the field stays open
// to say so), Escape or a click elsewhere leaves the name as it was.
const CellBuilderRenamePrompt: React.FC = () => {
  const prompt = useCellBuilderStore((s) => s.renamePrompt);
  const cell = useCellBuilderStore((s) => (prompt ? s.cells[prompt.cellId] : undefined));
  const close = useCellBuilderStore((s) => s.closeRenamePrompt);
  const [name, setName] = React.useState("");
  const [refused, setRefused] = React.useState<string | null>(null);
  const boxRef = React.useRef<HTMLFormElement>(null);

  React.useEffect(() => {
    setName(cell?.name ?? "");
    setRefused(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt]);

  React.useEffect(() => {
    if (!prompt) return;
    const onDown = (e: Event) => {
      if (!boxRef.current?.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [prompt, close]);

  if (!prompt || !cell) return null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next = name.trim();
    if (next === cell.name) return close();
    const st = useCellBuilderStore.getState();
    if (!next) return setRefused("A name is needed.");
    if (Object.values(st.cells).some((c) => c.id !== cell.id && c.name === next))
      return setRefused(`"${next}" is taken.`);
    st.renameCell(cell.id, next);
    close();
  };

  const left = Math.max(8, Math.min(prompt.x, window.innerWidth - 248));
  const top = Math.max(8, Math.min(prompt.y, window.innerHeight - 80));
  return createPortal(
    <form
      ref={boxRef}
      role="dialog"
      aria-label={`Rename ${cell.name}`}
      onSubmit={submit}
      className="fixed z-[70] w-60 rounded-sm border border-gray-700 bg-gray-800 p-2 text-xs text-gray-100 shadow-lg"
      style={{ left, top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <label className="flex flex-col gap-1">
        <span className="text-gray-400">Rename {cell.kind === "cell" ? "space" : cell.kind}</span>
        <input
          autoFocus
          className="rounded-sm border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
          value={name}
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => {
            setName(e.target.value);
            setRefused(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              close();
            }
          }}
        />
      </label>
      {refused && <div className="mt-1 text-amber-300">{refused}</div>}
    </form>,
    document.body,
  );
};

export default CellBuilderRenamePrompt;
