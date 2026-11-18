// A loft's stations and faces in the selection detail: editable station values and the
// faces each band of the loft makes.

import React from "react";
import { useCellBuilderStore, type BuilderCell, type BuilderSelection } from "@/state/cellBuilderStore";
import { bandFaceIds, type LoftBand } from "@/utils/cellbuilder/loft";
import { asMetaObject } from "@/utils/cellbuilder/metadata";
import { btn, inputCls } from "./fields";
import { MetadataFields } from "./MetadataFields";

// One editable station param bound to setLoftStationParam. Controlled by the
// live station value (regenerated on every edit); WIDTH/HEIGHT/RADIUS clamp to
// >= 0 in the store.
const LoftNumberField: React.FC<{
  label: string;
  member: string;
  stationIndex: number;
  paramKey: string;
  value: number;
  min?: number;
}> = ({ label, member, stationIndex, paramKey, value, min }) => {
  const setLoftStationParam = useCellBuilderStore((s) => s.setLoftStationParam);
  return (
    <label className="flex items-center gap-1">
      <span className="text-gray-400 w-12">{label}</span>
      <input
        type="number"
        step={0.1}
        min={min}
        className={`${inputCls} w-20`}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v))
            setLoftStationParam(member, stationIndex, paramKey, v);
        }}
      />
    </label>
  );
};

// One station's editable params (position Z/X/Y + the section dims for its
// TYPE). SEGMENTS/TYPE are left as authored (shape family stays put in 3a).
const EditableStation: React.FC<{
  label: string;
  member: string;
  stationIndex: number;
  station: import("@/utils/cellbuilder/loft").LoftStation;
}> = ({ label, member, stationIndex, station }) => (
  <div className="flex flex-col gap-0.5 border border-gray-700/50 rounded-sm p-1">
    <div className="flex items-center gap-1">
      <span className="text-gray-300 font-medium">{label}</span>
      <span className="text-gray-500">({station.TYPE})</span>
    </div>
    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
      <LoftNumberField
        label="Z"
        member={member}
        stationIndex={stationIndex}
        paramKey="Z"
        value={Number(station.Z ?? 0)}
      />
      <LoftNumberField
        label="X"
        member={member}
        stationIndex={stationIndex}
        paramKey="X"
        value={Number(station.X ?? 0)}
      />
      <LoftNumberField
        label="Y"
        member={member}
        stationIndex={stationIndex}
        paramKey="Y"
        value={Number(station.Y ?? 0)}
      />
      {station.TYPE === "circle" ? (
        <LoftNumberField
          label="Radius"
          member={member}
          stationIndex={stationIndex}
          paramKey="RADIUS"
          value={Number(station.RADIUS ?? 0)}
          min={0}
        />
      ) : (
        <>
          <LoftNumberField
            label="Width"
            member={member}
            stationIndex={stationIndex}
            paramKey="WIDTH"
            value={Number(station.WIDTH ?? 0)}
            min={0}
          />
          <LoftNumberField
            label="Height"
            member={member}
            stationIndex={stationIndex}
            paramKey="HEIGHT"
            value={Number(station.HEIGHT ?? 0)}
            min={0}
          />
        </>
      )}
    </div>
  </div>
);

// The band's per-face list: each side panel + end cap with an
// EXCLUDE checkbox bound to setLoftFaceExcluded. Checked = the face's
// member-relative id is in the member's EXCLUDE_FACES, i.e. its plate is dropped
// on recompile. The row order matches the 3D proxy's material groups
// (edge0..edgeN, cap_lo, cap_hi) so a face picked in the scene highlights its
// row. Mirrors the box SE{n} side-exclude idiom, but keyed by loft face id.
const LoftFaces: React.FC<{
  band: LoftBand;
  pickedFaceIndex?: number;
}> = ({ band, pickedFaceIndex }) => {
  const setLoftFaceExcluded = useCellBuilderStore((s) => s.setLoftFaceExcluded);
  const [open, setOpen] = React.useState(true);
  const { edges, caps } = React.useMemo(() => bandFaceIds(band), [band]);
  // Flat row order MUST match the swept-band material groups in
  // CellBuilderController (side panels, then cap_lo, cap_hi) so a scene pick's
  // faceIndex resolves to the right row.
  const rows = React.useMemo(
    () => [
      ...edges.map((id, k) => ({ id, label: `Side ${k}` })),
      { id: caps[0], label: "Bottom cap (cap_lo)" },
      { id: caps[1], label: "Top cap (cap_hi)" },
    ],
    [edges, caps],
  );
  const excluded = new Set(band.excludeFaces);
  const pickedId =
    pickedFaceIndex !== undefined && pickedFaceIndex < rows.length
      ? rows[pickedFaceIndex].id
      : undefined;
  return (
    <div className="border border-gray-700/50 rounded-sm p-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Faces</span>
        <span className="text-gray-400">({excluded.size} excluded)</span>
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 px-1 pt-1">
          {rows.map((row) => (
            <label
              key={row.id}
              className={
                "flex items-center gap-1 rounded-sm px-0.5 " +
                (row.id === pickedId ? "bg-rose-500/25" : "")
              }
              title={`Loft face ${band.member}:${row.id} — exclude to omit its plate at build`}
            >
              <input
                type="checkbox"
                checked={excluded.has(row.id)}
                onChange={(e) =>
                  setLoftFaceExcluded(band.member, row.id, e.target.checked)
                }
              />
              <span className={excluded.has(row.id) ? "text-gray-500" : ""}>
                {row.label}
              </span>
              {excluded.has(row.id) && (
                <span className="ml-auto text-amber-400/80">removed</span>
              )}
            </label>
          ))}
          <div className="text-gray-600 italic pt-0.5">
            Exclude drops the face's plate on recompile (interior end caps are
            unplated — excluding them is a no-op).
          </div>
        </div>
      )}
    </div>
  );
};

// Editable detail for a loft (swept-band) cell: the two bounding stations'
// params, insert/remove-station, a Move gizmo that translates the
// WHOLE member, and the per-face EXCLUDE list. A face picked in the
// scene (face select-mode) arrives as selection.faceIndex and highlights its
// row. Openings on loft faces remain deferred (backend geometry pending).
export const LoftInfo: React.FC<{
  cell: BuilderCell;
  selection: BuilderSelection;
}> = ({ cell, selection }) => {
  const band = cell.loft;
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const insertLoftStation = useCellBuilderStore((s) => s.insertLoftStation);
  const removeLoftStation = useCellBuilderStore((s) => s.removeLoftStation);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);
  const setLoftMemberMetadata = useCellBuilderStore((s) => s.setLoftMemberMetadata);
  if (!band) return null;
  const member = band.member;
  const memberDoc = loftMembers.find((m) => m.NAME === member);
  const loIndex = band.bay;
  const hiIndex = band.bay + 1;
  // 2 stations -> a single bay: deleting the hi station would drop below the
  // TopoLoftMember minimum, so disable it.
  const atMin = band.bandCount <= 1;
  return (
    <div className="flex flex-col gap-1.5 px-1 pt-1">
      <div className="text-gray-400">
        Loft band — member <span className="text-gray-200">{member}</span>, bay{" "}
        {band.bay + 1} of {band.bandCount}
      </div>
      <div
        className="flex items-center gap-1"
        title="Move the whole loft member (drag the widget in the scene, or use the grid nudge)"
      >
        <span className="text-gray-300">gizmo</span>
        {(
          [
            ["translate", "Move"],
            ["none", "Off"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            className={
              "px-1.5 py-0.5 rounded-sm " +
              (gizmoMode === m
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600")
            }
            onClick={() => setGizmoMode(m)}
            aria-pressed={gizmoMode === m}
          >
            {label}
          </button>
        ))}
      </div>
      <EditableStation
        label="Station"
        member={member}
        stationIndex={loIndex}
        station={band.stationLo}
      />
      <EditableStation
        label="→ next"
        member={member}
        stationIndex={hiIndex}
        station={band.stationHi}
      />
      <div className="flex items-center gap-1">
        <button
          className={btn}
          onClick={() => insertLoftStation(member, loIndex)}
          title="Insert a station after this bay's lo station (splits the bay in two)"
        >
          Insert station
        </button>
        <button
          className={btn}
          disabled={atMin}
          onClick={() => removeLoftStation(member, hiIndex)}
          title={
            atMin
              ? "A loft member needs at least 2 stations"
              : "Delete this bay's hi station (merges the adjacent bays)"
          }
        >
          Delete station
        </button>
      </div>
      <LoftFaces
        band={band}
        pickedFaceIndex={
          selection.kind === "face" ? selection.faceIndex : undefined
        }
      />
      {/* Member-level user metadata (shared by all bays of this loft member). */}
      <MetadataFields
        idPrefix={member}
        meta={asMetaObject(memberDoc?.METADATA)}
        onCommit={(next) => setLoftMemberMetadata(member, next ?? {})}
      />
    </div>
  );
};
