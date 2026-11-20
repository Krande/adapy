// A detail model's "Not built" report (Connections > Review, under "Detail models"): the joints its
// run did not build, grouped by why, and the seams of the joints it did build that are not in the
// model. A run of hundreds of joints that came back with a third of them used to say only "N
// skipped -- hover for why", one tooltip of hundreds of lines.
//
// GROUPED BY REASON, the numbers and names taken out (`groupNotBuilt`): most of a big run's reasons
// are one sentence with a different angle or name in it, and that sentence is the fact to act on.
// Each group shows its count and a few joints; opened, every joint, each a button that focuses its
// contact the way the identified joints' rows do (the contact ids ARE the joint ids).

import React, { useMemo, useState } from "react";

import { groupNotBuilt, weldFailuresByJoint, type DetailStats, type NotBuiltJoint } from "@/state/clashCheckStore";

/** How many joints a closed group names. */
const EXAMPLES = 3;
/** How many joints an open group lists before saying how many more there are. */
const LIST_LIMIT = 200;

/** A not-built or weld-failed joint's id: pressed, it focuses that contact. */
export const JointButton: React.FC<{ id: string; selected: boolean; title?: string; onFocus: (id: string) => void }> = ({
  id,
  selected,
  title,
  onFocus,
}) => (
  <button
    type="button"
    className={"font-mono hover:underline text-left " + (selected ? "text-blue-200" : "text-blue-300")}
    title={title ?? "Focus this contact"}
    data-not-built-joint={id}
    onClick={() => onFocus(id)}
  >
    {id}
  </button>
);

function jointTitle(j: NotBuiltJoint): string {
  const lines = [j.kind === "failed" ? `failed${j.spec ? ` in ${j.spec}` : ""}: ${j.reason}` : `declined: ${j.reason}`];
  for (const d of j.declined) lines.push(`${d.spec}: ${d.reason}`);
  return lines.join("\n");
}

export const NotBuiltReport: React.FC<{
  stats: DetailStats;
  /** The focused contact, drawn as such. */
  selected?: string | null;
  onFocusJoint: (jointId: string) => void;
}> = ({ stats, selected = null, onFocusJoint }) => {
  const groups = useMemo(() => groupNotBuilt(stats.notBuilt), [stats.notBuilt]);
  const welds = useMemo(() => weldFailuresByJoint(stats.weldFailures), [stats.weldFailures]);
  const [open, setOpen] = useState<string | null>(null);
  // A take-off from before the records were written: its sentences are all there is.
  const legacy = stats.notBuilt.length === 0 ? stats.skipped : [];

  if (groups.length === 0 && welds.size === 0 && legacy.length === 0) {
    return <div className="px-2 py-1 text-gray-500">Every joint was built, with every weld.</div>;
  }

  return (
    <div className="flex flex-col gap-2 px-2 py-1" aria-label="Not built">
      {groups.length > 0 && (
        <div className="flex flex-col">
          <div className="text-gray-300">
            <span className="font-semibold">{stats.notBuilt.length}</span> joint{stats.notBuilt.length === 1 ? "" : "s"} not built
          </div>
          {groups.map((g) => {
            const id = `${g.kind}:${g.key}`;
            const isOpen = open === id;
            return (
              <div key={id} className="border-t border-gray-800" data-not-built-group={g.kind}>
                <button
                  type="button"
                  className="flex w-full items-start gap-1 py-0.5 text-left hover:text-white"
                  title={g.example}
                  onClick={() => setOpen(isOpen ? null : id)}
                >
                  <span className="text-gray-500 shrink-0">{isOpen ? "▼" : "▶"}</span>
                  <span className={"shrink-0 " + (g.kind === "failed" ? "text-red-300/80" : "text-amber-200/80")}>
                    {g.kind}
                  </span>
                  <span className="flex-1 min-w-0 break-words text-gray-300">{g.example}</span>
                  <span className="shrink-0 tabular-nums text-gray-400">{g.joints.length}</span>
                </button>
                {!isOpen && (
                  <div className="pl-4 pb-0.5 text-gray-500 truncate">
                    e.g.{" "}
                    {g.joints.slice(0, EXAMPLES).map((j, i) => (
                      <React.Fragment key={j.jointId}>
                        {i > 0 && ", "}
                        <JointButton id={j.jointId} selected={selected === j.jointId} title={jointTitle(j)} onFocus={onFocusJoint} />
                      </React.Fragment>
                    ))}
                    {g.joints.length > EXAMPLES && ` and ${g.joints.length - EXAMPLES} more`}
                  </div>
                )}
                {isOpen && (
                  <ul className="pl-4 pb-1 flex flex-col">
                    {g.joints.slice(0, LIST_LIMIT).map((j) => (
                      <li key={j.jointId} className="flex items-start gap-2">
                        <JointButton id={j.jointId} selected={selected === j.jointId} title={jointTitle(j)} onFocus={onFocusJoint} />
                        <span className="flex-1 min-w-0 truncate text-gray-500" title={jointTitle(j)}>
                          {j.reason}
                        </span>
                      </li>
                    ))}
                    {g.joints.length > LIST_LIMIT && <li className="text-gray-500">and {g.joints.length - LIST_LIMIT} more</li>}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      {legacy.length > 0 && (
        <div className="flex flex-col">
          <div className="text-gray-300">
            <span className="font-semibold">{legacy.length}</span> joint{legacy.length === 1 ? "" : "s"} skipped
          </div>
          <ul className="flex flex-col">
            {legacy.slice(0, LIST_LIMIT).map((line) => (
              <li key={line} className="text-gray-500 break-words">
                {line}
              </li>
            ))}
          </ul>
        </div>
      )}

      {welds.size > 0 && (
        <div className="flex flex-col">
          <div className="text-gray-300">
            <span className="font-semibold">{stats.weldFailures.length}</span> weld{stats.weldFailures.length === 1 ? "" : "s"} failed
            <span className="text-gray-500"> — in {welds.size} built joint{welds.size === 1 ? "" : "s"}</span>
          </div>
          {[...welds.entries()].map(([jointId, rows]) => (
            <div key={jointId} className="border-t border-gray-800 py-0.5" data-weld-failures={jointId}>
              <JointButton id={jointId} selected={selected === jointId} onFocus={onFocusJoint} />
              <ul className="pl-4 flex flex-col">
                {rows.map((w, i) => (
                  <li key={`${w.place}-${i}`} className="flex items-start gap-2" title={w.connection}>
                    <span className="shrink-0 text-gray-300">{w.place || "—"}</span>
                    <span className="shrink-0 text-gray-400">{w.weldType || "—"}</span>
                    <span className="flex-1 min-w-0 break-words text-gray-500">{w.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default NotBuiltReport;
