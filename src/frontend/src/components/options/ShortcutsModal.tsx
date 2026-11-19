import React, { useEffect, useState, useSyncExternalStore } from "react";

import { OverlayPortal } from "@/components/ui/OverlayPortal";
import { CORE_SHORTCUT_GROUPS } from "@/layout/coreShortcutTable";
import { commandRank } from "@/layout/commands";
import { assignKeys, chordFromEvent, chordQueryRank, keyRows, parseKeyQuery, resetAllKeyBindings, setKeyBinding, type KeyRow } from "@/layout/keyBindings";
import { listShortcuts, shortcutBindingsVersion, subscribeShortcutBindings, type RegisteredShortcut } from "@/layout/shortcuts";
import { useCameraControlsStore } from "@/state/cameraControlsStore";
import { DRAG_MODIFIERS, MOUSE_BUTTONS, type CameraAction, type CameraMapping, type DragModifier } from "@/utils/camera/cameraMapping";

// The keyboard shortcuts drawn here all come from the shortcut registry
// (`layout/shortcuts`): core's own keys (`layout/coreShortcutTable`), a shell's
// and plugins', and non-core panels' toggles, grouped by the context in which
// they are live. Adding a shortcut is a registry entry, never an edit here. The
// pointer gestures below are not keys: they are read from the camera mapping in force.

export interface Shortcut {
  /** One or more key tokens rendered as separate <kbd> chips, e.g.
   *  ["Shift", "H"] -> "Shift + H". Alternatives get their own entry. */
  keys: string[];
  description: string;
}

export interface ShortcutGroup {
  context: string;
  /** Subtitle telling the user when this group's keys are live. */
  when: string;
  shortcuts: Shortcut[];
}

const DRAG_LABEL: Record<Exclude<CameraAction, "none">, string> = {
  orbit: "Orbit the view",
  pan: "Pan the view",
  zoom: "Zoom the view",
  select:
    "Box select: dragged rightwards a window (wholly inside), leftwards a crossing (touching); held with Shift or Ctrl it adds to the selection. In the cell builder it selects cells",
};
const MODIFIER_KEY: Record<DragModifier, string | null> = { none: null, shift: "Shift", ctrl: "Ctrl / \u2318", alt: "Alt" };

/** The mouse as the camera mapping in force has it (Settings > Controls): every drag that does something,
 *  the wheel, and the clicks. */
export function pointerGestures(mapping: CameraMapping): ShortcutGroup {
  const shortcuts: Shortcut[] = [];
  for (const button of MOUSE_BUTTONS) {
    for (const modifier of DRAG_MODIFIERS) {
      const action = mapping.drag[button][modifier];
      if (action === "none") continue;
      const drag = `${button[0].toUpperCase()}${button.slice(1)}-drag`;
      const mod = MODIFIER_KEY[modifier];
      shortcuts.push({ keys: mod ? [mod, drag] : [drag], description: DRAG_LABEL[action] });
    }
  }
  if (mapping.wheel.zoom) shortcuts.push({ keys: ["Wheel"], description: mapping.wheel.toCursor ? "Zoom towards the pointer" : "Zoom the view" });
  shortcuts.push(
    {
      keys: ["Ctrl / \u2318 / Shift", "Left-click"],
      description:
        "Add the clicked element (in the cell builder, the cell) to the selection, or take it out if it is selected -- as Ctrl+click does in the tree",
    },
    { keys: ["Right-click"], description: "The Scene's menu for what is under the pointer" },
  );
  return { context: "Mouse", when: "Over the 3D view, as Settings > Controls sets them (the controls profile)", shortcuts };
}

const DEFAULT_WHEN = "Active whenever the viewer has focus (ignored while typing in a field)";

const KEY_NAMES: Record<string, string> = {
  ctrl: "Ctrl / \u2318",
  cmd: "Ctrl / \u2318",
  escape: "Esc",
  arrowup: "\u2191",
  arrowdown: "\u2193",
  arrowleft: "\u2190",
  arrowright: "\u2192",
};

/** `"Ctrl+Shift+ArrowUp"` as the chips the dialog draws. */
function keyTokens(keys: string): string[] {
  return keys.split("+").map((k) => KEY_NAMES[k.trim().toLowerCase()] ?? k.trim());
}

/** Every registered shortcut as the dialog's groups (the mouse gestures aside): core's in their fixed order, then the others in first-seen order. */
export function shortcutDialogGroups(customisable: boolean): ShortcutGroup[] {
  const byGroup = new Map<string, Shortcut[]>();
  for (const { group } of CORE_SHORTCUT_GROUPS) byGroup.set(group, []);
  for (const s of listShortcuts()) {
    if (s.requires === "customisable-panels" && !customisable) continue;
    const g = s.group ?? "Shell and plugins";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g)!.push({ keys: keyTokens(s.keysLabel ?? s.keys), description: s.label });
  }
  const when = new Map(CORE_SHORTCUT_GROUPS.map((g) => [g.group, g.when]));
  return [...byGroup]
    .filter(([, shortcuts]) => shortcuts.length > 0)
    .map(([context, shortcuts]) => ({ context, when: when.get(context) ?? DEFAULT_WHEN, shortcuts }));
}
const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="inline-block rounded border border-gray-600 bg-gray-800 px-1.5 py-0.5 text-xs font-mono text-gray-100 shadow-sm">
    {children}
  </kbd>
);

const KeyCombo: React.FC<{ keys: string[] }> = ({ keys }) => (
  <span className="inline-flex items-center gap-1 whitespace-nowrap">
    {keys.map((k, i) => (
      <React.Fragment key={i}>
        {i > 0 && <span className="text-gray-500">+</span>}
        <Kbd>{k}</Kbd>
      </React.Fragment>
    ))}
  </span>
);

// Open from elsewhere too (a shell's Help menu): a tiny external store the host below follows.
let externalOpen = false;
const openListeners = new Set<() => void>();
const setExternalOpen = (v: boolean) => {
  if (externalOpen === v) return;
  externalOpen = v;
  for (const l of openListeners) l();
};

/** Open the shortcuts dialog from outside the Options panel; `ShortcutsDialogHost` draws it. */
export function openShortcutsDialog(): void {
  setExternalOpen(true);
}

/** Mount once (a shell's chrome does) to draw the dialog `openShortcutsDialog` opens. */
export const ShortcutsDialogHost: React.FC = () => {
  const open = React.useSyncExternalStore(
    (cb) => {
      openListeners.add(cb);
      return () => openListeners.delete(cb);
    },
    () => externalOpen,
    () => false,
  );
  return open ? <ShortcutsDialog onClose={() => setExternalOpen(false)} /> : null;
};

const ShortcutsModal: React.FC = () => {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded-sm w-full"
        onClick={() => setOpen(true)}
      >
        Shortcut Keys
      </button>
      {open && <ShortcutsDialog onClose={() => setOpen(false)} />}
    </div>
  );
};

/**
 * The dialog's rows that answer `query`. Keys, when the query reads as keys (`parseKeyQuery`):
 * "alt" every chord with Alt, "g" every chord on G, "ctrl+k" / "shift h" that chord first --
 * a single letter is a key, not every label with a "g" in it. Words otherwise: the name, group
 * or keyword (`commandRank`). A key's name that is also a word ("delete") finds both.
 */
export function shortcutRowsFor(query: string, rows: readonly KeyRow[]): KeyRow[] {
  const keys = parseKeyQuery(query);
  return rows
    .map((r) => {
      const byKey = keys ? chordQueryRank(query, r) : null;
      const byName =
        !keys || keys.wordToo ? commandRank(query, { id: r.commandId ?? r.shortcutId, label: r.label, group: r.group, keywords: r.keywords }) : null;
      const rank = byKey !== null ? byKey - 2 : byName;
      return { r, rank };
    })
    .filter((x): x is { r: KeyRow; rank: number } => x.rank !== null)
    .sort((a, b) => a.rank - b.rank || a.r.group.localeCompare(b.r.group) || a.r.label.localeCompare(b.r.label))
    .map((x) => x.r);
}

/** A row's other chords for the same command (a shell's alias), shown after the one in force. */
const ExtraChords: React.FC<{ row: KeyRow }> = ({ row }) =>
  row.extraKeys?.length ? (
    <span className="inline-flex items-center gap-1 text-xs text-gray-400" title="Also runs it (the shell's own key)" data-extra-chords="">
      or
      {row.extraKeys.map((k) => (
        <KeyCombo key={k} keys={keyTokens(k)} />
      ))}
    </span>
  ) : null;

/** A key row's chord button: click, then press the new combination; Escape cancels. */
const ChordCapture: React.FC<{ row: KeyRow; onAssign: (keys: string) => void }> = ({ row, onAssign }) => {
  const [capturing, setCapturing] = useState(false);
  if (row.locked) {
    const shown = row.keysLabel ?? row.keys;
    return (
      <span className="inline-flex items-center" title="A widget's own key: not editable">
        {shown ? <KeyCombo keys={keyTokens(shown)} /> : null}
      </span>
    );
  }
  return (
    <button
      type="button"
      data-chord-button={row.shortcutId}
      className={`min-w-24 rounded-sm border px-2 py-0.5 text-left ${capturing ? "border-blue-400 bg-blue-900/40" : "border-gray-700 hover:border-gray-500"}`}
      title="Click, then press the new key combination (Esc cancels)"
      onClick={() => setCapturing(true)}
      onBlur={() => setCapturing(false)}
      onKeyDown={(e) => {
        if (!capturing) return;
        // The press is the new chord: nothing else sees it.
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "Escape") return setCapturing(false);
        const chord = chordFromEvent(e);
        if (!chord) return;
        setCapturing(false);
        onAssign(chord);
      }}
    >
      {capturing ? <span className="text-xs text-blue-200">Press keys…</span> : row.keys ? <KeyCombo keys={keyTokens(row.keys)} /> : <span className="text-xs text-gray-500">none</span>}
    </button>
  );
};

/** The dialog itself, open while mounted: every command with its key, editable. */
const ShortcutsDialog: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  useSyncExternalStore(subscribeShortcutBindings, shortcutBindingsVersion, shortcutBindingsVersion);
  const gestures = pointerGestures(useCameraControlsStore((s) => s.mapping));
  const [query, setQuery] = useState("");
  const [problem, setProblem] = useState<{ row: KeyRow; keys: string; reason: string; holder?: RegisteredShortcut } | null>(null);
  const rows = keyRows();
  const shown = shortcutRowsFor(query, rows);
  const assign = (row: KeyRow, keys: string) => {
    const res = assignKeys(row.shortcutId, keys);
    setProblem(res.ok ? null : { row, keys, reason: res.reason, holder: res.conflict });
  };

  // Escape closes the dialog (only wired while open so it doesn't shadow the viewer's own Escape handling).
  const close = React.useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <OverlayPortal>
      <div className="fixed inset-0 z-[70] flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto" onClick={onClose}>
        <div
          className="bg-gray-900 border border-gray-700 rounded-md shadow-xl flex flex-col w-full max-w-3xl max-h-[85vh] my-auto text-white"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="Keyboard shortcuts"
        >
          <div className="flex items-center gap-3 border-b border-gray-700 px-4 py-3">
            <h2 className="flex-1 text-base font-bold">Keyboard shortcuts</h2>
            <button type="button" className="rounded-sm border border-gray-600 px-2 py-0.5 text-xs hover:bg-gray-800" onClick={resetAllKeyBindings} data-reset-all="">
              Reset all
            </button>
            <button type="button" className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2" onClick={onClose} aria-label="Close" title="Close (Esc)">
              ×
            </button>
          </div>
          <div className="border-b border-gray-800 px-4 py-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search commands (name, group, keyword) or keys (ctrl+k, alt, g)"
              aria-label="Search shortcuts"
              className="w-full rounded-sm bg-gray-800 px-2 py-1 text-sm outline-none"
              autoFocus
            />
            {problem && (
              <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 rounded-sm bg-amber-900/40 px-2 py-1 text-xs text-amber-100" data-key-problem="">
                <span className="flex-1">{problem.reason}</span>
                {problem.holder && (
                  <button
                    type="button"
                    className="rounded-sm border border-amber-400 px-2 py-0.5 hover:bg-amber-800/50"
                    onClick={() => {
                      assignKeys(problem.row.shortcutId, problem.keys, { replace: true });
                      setProblem(null);
                    }}
                  >
                    Replace
                  </button>
                )}
                <button type="button" className="rounded-sm border border-gray-500 px-2 py-0.5 hover:bg-gray-800" onClick={() => setProblem(null)}>
                  Cancel
                </button>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <ul className="divide-y divide-gray-800 rounded-sm border border-gray-800" data-shortcut-rows="">
              {shown.length === 0 && <li className="px-3 py-2 text-xs text-gray-400">No command matches.</li>}
              {shown.map((row) => (
                <li key={row.shortcutId} data-shortcut-row={row.shortcutId} className="flex items-center gap-3 px-3 py-1.5">
                  <span className="w-20 shrink-0 truncate text-xs text-gray-400">{row.group}</span>
                  <span className="flex-1 truncate text-sm text-gray-200" title={row.commandId ?? row.shortcutId}>
                    {row.label}
                  </span>
                  <ChordCapture row={row} onAssign={(keys) => assign(row, keys)} />
                  <ExtraChords row={row} />
                  <button
                    type="button"
                    className="w-14 text-xs text-gray-400 hover:text-white disabled:opacity-30"
                    disabled={row.locked || !row.keys}
                    title="Leave this command without a key"
                    onClick={() => setKeyBinding(row.shortcutId, null)}
                  >
                    Unbind
                  </button>
                  <button
                    type="button"
                    className="w-12 text-xs text-gray-400 hover:text-white disabled:opacity-30"
                    disabled={!row.overridden}
                    title="Back to the default key"
                    data-reset-row={row.shortcutId}
                    onClick={() => setKeyBinding(row.shortcutId, undefined)}
                  >
                    Reset
                  </button>
                </li>
              ))}
            </ul>
            <section>
              <h3 className="text-sm font-semibold text-blue-300">{gestures.context}</h3>
              <p className="mb-2 text-xs text-gray-400">{gestures.when}</p>
              <ul className="divide-y divide-gray-800 rounded-sm border border-gray-800">
                {gestures.shortcuts.map((sc, i) => (
                  <li key={i} className="flex items-center justify-between gap-4 px-3 py-1.5">
                    <span className="text-sm text-gray-200">{sc.description}</span>
                    <KeyCombo keys={sc.keys} />
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </OverlayPortal>
  );
};
export default ShortcutsModal;
