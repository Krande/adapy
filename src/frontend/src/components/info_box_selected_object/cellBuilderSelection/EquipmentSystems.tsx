// The systems an equipment item is connected to, with their ports, in the selection detail.

import React from "react";
import { useCellBuilderStore } from "@/state/cellBuilderStore";

const SYSTEM_TYPE_COLOR: Record<string, string> = {
  piping: "#38bdf8",
  duct: "#a3e635",
  cable: "#c084fc",
  electrical: "#f59e0b",
};

// Systems this equipment participates in — which system it's connected to, via
// which port. Its own collapsible section.
const PORT_CATEGORY_COLOR: Record<string, string> = {
  process: "#38bdf8",
  electrical: "#f59e0b",
  signal: "#ec4899",
};

export const EquipmentSystems: React.FC<{
  equipmentName: string;
  equipmentType?: string;
}> = ({ equipmentName, equipmentType }) => {
  const systems = useCellBuilderStore((st) => st.systems);
  const equipmentTypes = useCellBuilderStore((st) => st.equipmentTypes);
  const [open, setOpen] = React.useState(true);
  const connected = Object.values(systems).filter((sys) =>
    sys.connections.some((c) => c.equipment === equipmentName),
  );

  // This equipment's type ports, and which are wired up by a system — so the
  // unconnected I/O (what the red "!" overlay flags) can be listed explicitly.
  const ports = React.useMemo(() => {
    if (!equipmentType) return [];
    const key = equipmentType.toLowerCase();
    const t =
      equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
      equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return t?.ports ?? [];
  }, [equipmentType, equipmentTypes]);
  const connectedPorts = new Set<string>();
  for (const sys of Object.values(systems))
    for (const c of sys.connections)
      if (c.equipment === equipmentName && c.port) connectedPorts.add(c.port);
  const unconnected = ports.filter((p) => !connectedPorts.has(p.name));

  return (
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Systems & I/O</span>
        <span className="text-gray-400">({connected.length} wired)</span>
        {unconnected.some((p) => p.direction === "IN") && (
          <span className="ml-1 text-red-400" title="Has unconnected input(s)">
            ⚠
          </span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-1 pt-1">
          {connected.length === 0 ? (
            <div className="text-gray-500 italic">
              Not connected to any system.
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {connected.map((sys) => {
                const cports = sys.connections
                  .filter((c) => c.equipment === equipmentName)
                  .map((c) => c.port);
                return (
                  <div key={sys.id} className="flex items-center gap-1">
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ background: SYSTEM_TYPE_COLOR[sys.type] }}
                    />
                    <span className="truncate">{sys.name}</span>
                    <span className="text-gray-400">({sys.type})</span>
                    <span className="ml-auto text-gray-400">
                      {cports.join(", ")}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {/* Unconnected ports — inputs (IN) are the "missing" ones the overlay
              warns about; outputs/signals are shown too but not flagged. */}
          {unconnected.length > 0 && (
            <div className="flex flex-col gap-0.5 border-t border-gray-700/50 pt-1">
              <span className="text-gray-400">Unconnected I/O</span>
              {unconnected.map((p) => (
                <div key={p.name} className="flex items-center gap-1">
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ background: PORT_CATEGORY_COLOR[p.category] }}
                  />
                  <span
                    className={
                      "truncate " + (p.direction === "IN" ? "text-red-300" : "")
                    }
                  >
                    {p.name}
                  </span>
                  <span className="ml-auto text-gray-500">
                    {p.direction} · {p.category}
                    {p.direction === "IN" ? " · missing" : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
          {equipmentType && ports.length === 0 && (
            <div className="text-gray-600 italic">
              Port list unavailable (type not loaded).
            </div>
          )}
        </div>
      )}
    </div>
  );
};
