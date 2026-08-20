import React from "react";
import {useCellBuilderStore, type SystemConnection} from "@/state/cellBuilderStore";
import {typePickerItems} from "@/utils/cellbuilder/ports";
import {PositionedMenu} from "@/components/common/PositionedMenu";
import {btn, btnGray, inputCls} from "./chrome";

// Standard archetype ports (kept in sync with ada.topo_model.equipment); a
// free-text fallback covers custom equipment.
const ARCHETYPE_PORTS: Record<string, string[]> = {
  pump: ["suction", "discharge", "power", "signal"],
  tank: ["inlet", "outlet", "signal"],
};

/** Axis labels → outward unit vector for a site terminal's orientation. */
const ORIENT_VECTORS: Record<string, [number, number, number]> = {
  "+X": [1, 0, 0],
  "-X": [-1, 0, 0],
  "+Y": [0, 1, 0],
  "-Y": [0, -1, 0],
  "+Z": [0, 0, 1],
  "-Z": [0, 0, -1],
};

/** Nearest axis label for a direction vector, for compact display (falls back
 * to the raw tuple when it isn't axis-aligned). */
export const orientLabel = (v: [number, number, number]): string => {
  for (const [k, av] of Object.entries(ORIENT_VECTORS)) {
    if (av[0] === v[0] && av[1] === v[1] && av[2] === v[2]) return k;
  }
  return v.join(",");
};

export const ConnectionAdder: React.FC<{
  equipmentNames: string[];
  onAdd: (conn: SystemConnection) => void;
}> = ({ equipmentNames, onAdd }) => {
  const cells = useCellBuilderStore((st) => st.cells);
  // Endpoint mode: an equipment port, or a site terminal (model-boundary I/O).
  const [mode, setMode] = React.useState<"equip" | "site">("equip");
  const [eq, setEq] = React.useState("");
  const [port, setPort] = React.useState("");
  const eqType = Object.values(cells).find((c) => c.name === eq)?.equipmentType;
  const portOptions = eqType ? (ARCHETYPE_PORTS[eqType] ?? []) : [];
  const [siteName, setSiteName] = React.useState("");
  const [pos, setPos] = React.useState<[string, string, string]>([
    "0",
    "0",
    "0",
  ]);
  const [dir, setDir] = React.useState<"IN" | "OUT">("IN");
  // Orientation: the outward nozzle vector the run leaves the terminal along.
  // A terminal on the x=0 wall should face +X (into the model), etc.
  const [orient, setOrient] = React.useState<keyof typeof ORIENT_VECTORS>("+X");

  const modeBtn = (m: "equip" | "site", label: string) => (
    <button
      key={m}
      className={
        "px-1.5 py-0.5 rounded-sm text-[10px] " +
        (mode === m
          ? "bg-accent text-white"
          : "bg-surface-2 text-content hover:bg-surface-3")
      }
      onClick={() => setMode(m)}
      aria-pressed={mode === m}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-col gap-1 pl-3">
      <div className="flex items-center gap-1">
        <span className="text-content-subtle text-[10px]">add</span>
        {modeBtn("equip", "equipment")}
        {modeBtn("site", "site I/O")}
      </div>
      {mode === "equip" ? (
        <div className="flex items-center gap-1">
          <select
            className={`${inputCls} min-w-0 flex-1`}
            value={eq}
            onChange={(e) => {
              setEq(e.target.value);
              setPort("");
            }}
          >
            <option value="">equipment…</option>
            {equipmentNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {portOptions.length > 0 ? (
            <select
              className={inputCls}
              value={port}
              onChange={(e) => setPort(e.target.value)}
            >
              <option value="">port…</option>
              {portOptions.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : (
            <input
              className={`${inputCls} w-20`}
              placeholder="port"
              value={port}
              onChange={(e) => setPort(e.target.value)}
            />
          )}
          <button
            className="px-1.5 py-0.5 rounded-sm bg-accent text-white disabled:opacity-40"
            disabled={!eq || !port}
            onClick={() => {
              onAdd({ equipment: eq, port });
              setPort("");
            }}
          >
            +
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1 flex-wrap">
          <input
            className={`${inputCls} w-24`}
            placeholder="site name"
            value={siteName}
            onChange={(e) => setSiteName(e.target.value)}
          />
          {([0, 1, 2] as const).map((i) => (
            <input
              key={i}
              type="number"
              step={0.5}
              className={`${inputCls} w-12`}
              title={["x", "y", "z"][i]}
              value={pos[i]}
              onChange={(e) =>
                setPos((p) => {
                  const next = [...p] as [string, string, string];
                  next[i] = e.target.value;
                  return next;
                })
              }
            />
          ))}
          <select
            className={inputCls}
            value={dir}
            onChange={(e) => setDir(e.target.value as "IN" | "OUT")}
            title="Site input (into the model) or output (off the model)"
          >
            <option value="IN">IN</option>
            <option value="OUT">OUT</option>
          </select>
          <select
            className={inputCls}
            value={orient}
            onChange={(e) =>
              setOrient(e.target.value as keyof typeof ORIENT_VECTORS)
            }
            title="Orientation — the outward direction the run leaves the terminal along"
          >
            {Object.keys(ORIENT_VECTORS).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <button
            className="px-1.5 py-0.5 rounded-sm bg-accent text-white disabled:opacity-40"
            disabled={!siteName.trim()}
            onClick={() => {
              onAdd({
                site: siteName.trim(),
                position: [
                  Number(pos[0]) || 0,
                  Number(pos[1]) || 0,
                  Number(pos[2]) || 0,
                ],
                direction: dir,
                directionVector: ORIENT_VECTORS[orient],
              });
              setSiteName("");
            }}
          >
            +
          </button>
        </div>
      )}
    </div>
  );
};
