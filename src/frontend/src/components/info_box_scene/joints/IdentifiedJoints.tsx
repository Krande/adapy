// The identified joints, as a keyboard-navigable tree: connection-type groups, the open group's
// joints under it, and the focused joint's detail under that.
//
// WHY A TREE AND NOT A TABLE. A hundred joints in eight kinds is read kind-first -- and the facts
// about one joint (its members, their sections, which generators bind it) are a block, not a row.
// See `JointDetail`.
//
// KEYS. Up/Down step through what is ON SCREEN and change nothing else -- a group the cursor
// passes stays closed, or the only way to reach the fourth kind of joint would be to walk every
// joint of the first three. Right OPENS: a closed group, then (pressed again) steps into its
// first joint, and on a joint its detail. Left closes, and from a joint whose detail is already
// closed it goes up to the group row, which is where a person pressing it twice expects to end
// up. Moving the cursor onto a JOINT selects its members in the model and grows its marker, so
// holding Down walks the frame joint by joint: the list and the 3D view are one surface.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  checkedSourceName,
  detailBatches,
  groupColor,
  memberNamesForGroup,
  useClashCheckStore,
  type ClashResult,
} from "@/state/clashCheckStore";
import { useModelState } from "@/state/modelState";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { focusJoint as focusJointEverywhere } from "@/utils/scene/clashJointFocus";
import { selectInOtherModel } from "@/utils/scene/crossModelSelect";
import IsolationControls from "./IsolationControls";
import JointDetail from "./JointDetail";
import { cursorIndex, groupRowFor, step, visibleRows, type JointRow } from "./rows";

const IdentifiedJoints: React.FC = () => {
  const result = useClashCheckStore((s) => s.result);
  const openGroup = useClashCheckStore((s) => s.selectedGroup);
  const focusedJoint = useClashCheckStore((s) => s.selectedJoint);
  const selectGroup = useClashCheckStore((s) => s.selectGroup);
  const runDetailAll = useClashCheckStore((s) => s.runDetailAll);
  const detailBusy = useClashCheckStore((s) => s.detailBusy);
  const detailProgress = useClashCheckStore((s) => s.detailProgress);
  const checkedSource = useClashCheckStore((s) => s.sourceName);
  const loadedSourceName = useModelState((s) => s.loadedSourceName);
  // The model the CHECK ran against -- see `checkedSourceName`. After a detail run the loaded
  // source is the produced overlay, which carries none of these member names.
  const sourceName = checkedSourceName(checkedSource, loadedSourceName);
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const [detailOpen, setDetailOpen] = useState(true);
  // The cursor is the tree's own state, deliberately NOT the open group: moving must not expand.
  const [cursorId, setCursorId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const rows = useMemo(() => (result ? visibleRows(result, openGroup) : []), [result, openGroup]);
  const cursor = useMemo(
    () => cursorIndex(rows, cursorId, openGroup, focusedJoint),
    [rows, cursorId, openGroup, focusedJoint],
  );

  const goToJoint = useCallback(
    (jointId: string) => {
      void focusJointEverywhere(jointId);
    },
    [],
  );

  // Keep the cursor row in view when it moved by keyboard rather than by click.
  useEffect(() => {
    if (cursor < 0) return;
    const row = listRef.current?.querySelector(`[data-row-index="${cursor}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [cursor, rows]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!result || rows.length === 0) return;
    const current = rows[cursor] as JointRow | undefined;
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault();
        const next = step(rows, cursor, e.key === "ArrowDown" ? 1 : -1);
        if (!next) return;
        setCursorId(next.id);
        // A group under the cursor is NOT opened by moving to it. Only a joint does anything
        // besides move, and what it does is select itself -- which is the point of stepping
        // through joints at all.
        if (next.kind === "joint") goToJoint(next.jointId);
        return;
      }
      case "ArrowRight": {
        e.preventDefault();
        if (!current) return;
        if (current.kind === "group") {
          if (openGroup !== current.typeKey) {
            selectGroup(current.typeKey); // open it, cursor stays on the header
          } else {
            const first = rows.find((r) => r.kind === "joint" && r.typeKey === current.typeKey);
            if (first?.kind === "joint") {
              setCursorId(first.id);
              goToJoint(first.jointId);
            }
          }
        } else {
          setDetailOpen(true);
        }
        return;
      }
      case "ArrowLeft": {
        e.preventDefault();
        if (!current) return;
        if (current.kind === "joint") {
          if (detailOpen) {
            setDetailOpen(false);
            return;
          }
          const owner = groupRowFor(rows, current);
          if (owner) setCursorId(owner.id); // up to the parent; a second Left collapses it
        } else if (openGroup === current.typeKey) {
          selectGroup(null);
        }
        return;
      }
      case "Enter":
      case " ": {
        if (!current) return;
        e.preventDefault();
        if (current.kind === "joint") {
          setDetailOpen((v) => !v);
          goToJoint(current.jointId);
        } else {
          selectGroup(openGroup === current.typeKey ? null : current.typeKey);
        }
        return;
      }
      default:
        return;
    }
  };

  if (!result || result.joints.length === 0) return null;

  const withGenerator = detailBatches(result).reduce((n, b) => n + b.jointIds.length, 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <div className="text-gray-300 flex-1 min-w-0">
          <span className="font-semibold">{result.joints.length}</span> identified joint
          {result.joints.length === 1 ? "" : "s"} <span className="text-gray-500">— could be detailed (Clashes)</span>
        </div>
        <button
          type="button"
          disabled={detailBusy || withGenerator === 0}
          title={
            withGenerator === 0
              ? "No joint in this result has a generator that could detail it"
              : `Detail all ${withGenerator} joints that have a matching generator`
          }
          className={`shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] ${
            detailBusy || withGenerator === 0
              ? "bg-gray-700 text-gray-500 cursor-not-allowed"
              : "bg-emerald-800 text-emerald-100 hover:bg-emerald-700"
          }`}
          onClick={() => void runDetailAll(scope)}
        >
          {detailBusy && detailProgress
            ? `generating ${detailProgress.done}/${detailProgress.total}…`
            : `generate detail model (${withGenerator})`}
        </button>
      </div>

      <IsolationControls />

      <div
        ref={listRef}
        tabIndex={0}
        role="tree"
        aria-label="Identified joints"
        className="flex flex-col outline-none focus:ring-1 focus:ring-blue-600/60 rounded-sm"
        onKeyDown={onKeyDown}
      >
        {rows.map((row, index) => (
          <Row
            key={row.id}
            row={row}
            index={index}
            active={index === cursor}
            result={result}
            openGroup={openGroup}
            detailOpen={detailOpen}
            scope={scope}
            sourceName={sourceName}
            onToggleGroup={(typeKey) => {
              setCursorId(`g:${typeKey}`);
              selectGroup(openGroup === typeKey ? null : typeKey);
            }}
            onGoToJoint={(jointId) => {
              setCursorId(`j:${jointId}`);
              goToJoint(jointId);
            }}
            onToggleDetail={() => setDetailOpen((v) => !v)}
          />
        ))}
      </div>
      <div className="text-[10px] text-gray-500 px-1">↑↓ move · → open · ← close · ⏎ detail</div>
    </div>
  );
};

const Row: React.FC<{
  row: JointRow;
  index: number;
  active: boolean;
  result: ClashResult;
  openGroup: string | null;
  detailOpen: boolean;
  scope: string;
  sourceName: string | null;
  onToggleGroup: (typeKey: string) => void;
  onGoToJoint: (jointId: string) => void;
  onToggleDetail: () => void;
}> = ({ row, index, active, result, openGroup, detailOpen, scope, sourceName, onToggleGroup, onGoToJoint, onToggleDetail }) => {
  const ring = active ? "bg-blue-900/40" : "";
  if (row.kind === "group") {
    const group = result.groups.find((g) => g.typeKey === row.typeKey);
    if (!group) return null;
    const open = openGroup === group.typeKey;
    return (
      <div data-row-index={index} role="treeitem" aria-expanded={open} className={`border-t border-gray-700 ${ring}`}>
        <div className="flex items-center gap-2 px-1 py-1">
          <span
            className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: groupColor(result, group.typeKey) }}
            title="This group's colour in the 3D view"
          />
          <button
            type="button"
            className="flex-1 min-w-0 text-left text-xs text-gray-100 hover:text-white truncate"
            onClick={() => onToggleGroup(group.typeKey)}
            title={group.typeKey}
          >
            {open ? "▼" : "▶"} {group.typeLabel}
          </button>
          <span className="text-xs text-gray-400 tabular-nums">{group.count}</span>
          <button
            type="button"
            className="text-[10px] text-blue-300 hover:text-white"
            title="Select every member in this group"
            onClick={() => {
              if (!sourceName) return;
              void selectInOtherModel({ file: sourceName, nodeNames: [...memberNamesForGroup(result, group.typeKey)] });
            }}
          >
            select all
          </button>
        </div>
      </div>
    );
  }

  const joint = result.jointsById.get(row.jointId);
  if (!joint) return null;
  const showDetail = active && detailOpen;
  return (
    <div data-row-index={index} role="treeitem" aria-expanded={showDetail} className={`border-t border-white/5 ${ring}`}>
      <button
        type="button"
        className="flex w-full items-center gap-2 pl-3 pr-1 py-0.5 text-[11px] text-left"
        onClick={() => {
          onGoToJoint(joint.id);
          if (active) onToggleDetail();
        }}
        title={joint.members.map((m) => m.name).join(", ")}
      >
        <span className="text-gray-500 shrink-0">{showDetail ? "▼" : "▶"}</span>
        <span className="truncate flex-1 min-w-0 text-blue-300">{joint.members.map((m) => m.name).join(" + ")}</span>
        <span className="text-gray-500 shrink-0">{joint.applicable.length ? `${joint.applicable.length} spec` : "—"}</span>
      </button>
      {showDetail && (
        <JointDetail
          joint={joint}
          result={result}
          scope={scope}
          onSelectMembers={() => {
            if (!sourceName) return;
            void selectInOtherModel({ file: sourceName, nodeNames: joint.members.map((m) => m.name) });
          }}
        />
      )}
    </div>
  );
};

export default IdentifiedJoints;
