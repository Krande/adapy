// One identified joint, in full: what meets there, where, and what could detail it.
//
// This replaces a wide per-joint table row. A joint's facts are a handful of members with their
// own sections and types plus a list of specs -- a column layout either truncates them or forces
// the panel wider than the screen it sits on, and the one thing a person opens a joint to read is
// exactly the part that gets truncated.

import React from "react";

import { groupColor, specForJoint, useClashCheckStore, type ClashJoint, type ClashResult } from "@/state/clashCheckStore";
import { isSpecAvailable } from "@/state/clashCheckStore";

const fmt = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");

const JointDetail: React.FC<{
  joint: ClashJoint;
  result: ClashResult;
  scope: string;
  onSelectMembers: () => void;
}> = ({ joint, result, scope, onSelectMembers }) => {
  const detailBusy = useClashCheckStore((s) => s.detailBusy);
  const detailSpec = useClashCheckStore((s) => s.detailSpec);
  const runDetail = useClashCheckStore((s) => s.runDetail);
  const best = specForJoint(joint);
  // `null` for the live capability set: the union route does not exist yet, so this fails OPEN
  // rather than hiding a spec that may well run (see `isSpecAvailable`).
  const available = best ? isSpecAvailable(best, null) : false;
  const busyHere = detailBusy && detailSpec === best?.spec;

  return (
    <div className="pl-5 pr-1 pb-2 pt-1 text-[11px] flex flex-col gap-1.5 bg-black/20">
      <div className="flex items-center gap-2 text-gray-400">
        <span
          className="inline-block h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: groupColor(result, joint.typeKey) }}
        />
        <span className="truncate" title={joint.typeKey}>
          {joint.typeLabel}
        </span>
        <span className="ml-auto font-mono text-gray-500">{joint.id.slice(0, 8)}</span>
      </div>

      <div className="text-gray-400">
        centre <span className="tabular-nums text-gray-300">{fmt(joint.centre[0])}</span>,{" "}
        <span className="tabular-nums text-gray-300">{fmt(joint.centre[1])}</span>,{" "}
        <span className="tabular-nums text-gray-300">{fmt(joint.centre[2])}</span>
      </div>

      <div className="flex flex-col gap-0.5">
        <div className="text-gray-500">members</div>
        {joint.members.map((m) => (
          <div key={`${m.name}-${m.guid ?? ""}`} className="flex items-center gap-2 text-gray-300">
            <span className="truncate flex-1 min-w-0" title={m.guid ?? m.name}>
              {m.name}
            </span>
            <span className="text-gray-500 shrink-0">{m.kind}</span>
            {m.section && <span className="text-gray-500 shrink-0">{m.section}</span>}
            {m.memberType && <span className="text-gray-500 shrink-0">{m.memberType}</span>}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-0.5">
        <div className="text-gray-500">generators</div>
        {joint.applicable.length === 0 ? (
          <div className="text-gray-500 italic">No registered spec matches this joint.</div>
        ) : (
          joint.applicable.map((spec) => (
            <div key={spec.spec} className="flex items-center gap-2 text-gray-300">
              <span className="truncate flex-1 min-w-0">{spec.spec}</span>
              <span className="text-gray-500 shrink-0">
                {spec.capability ? `pool: ${spec.capability}` : "built-in"}
              </span>
            </div>
          ))
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          className="rounded-sm px-1.5 py-0.5 bg-gray-700 text-gray-200 hover:bg-gray-600"
          onClick={onSelectMembers}
        >
          select members
        </button>
        <button
          type="button"
          disabled={!best || !available || detailBusy}
          title={
            best
              ? available
                ? `Detail this joint with ${best.spec}`
                : `No live pool advertises '${best.capability}'`
              : "No registered spec matches this joint"
          }
          className={`rounded-sm px-1.5 py-0.5 ${
            best && available && !detailBusy
              ? "bg-emerald-800 text-emerald-100 hover:bg-emerald-700"
              : "bg-gray-700 text-gray-500 cursor-not-allowed"
          }`}
          onClick={() => {
            if (!best || !available) return;
            void runDetail(scope, [joint.id], best.spec);
          }}
        >
          {busyHere ? "generating…" : "generate detail"}
        </button>
      </div>
    </div>
  );
};

export default JointDetail;
