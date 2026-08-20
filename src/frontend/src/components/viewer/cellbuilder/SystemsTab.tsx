import React from "react";
import {useCellBuilderStore, type SystemConnection} from "@/state/cellBuilderStore";
import {useEquipmentCatalogStore} from "@/state/equipmentCatalogStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {highlightSystems, revertSystemHighlight, systemColorHex} from "@/utils/viewer/systemColors";
import {ConnectionAdder, orientLabel} from "./ConnectionAdder";
import {Section} from "./boxedSection";
import {btn, btnGray, inputCls} from "./chrome";

// The whole Systems tab body: list the service runs, their type, and which
// equipment ports each connects. Add/remove systems and connections; highlight
// each run in its own colour. Mounted on every tab (hidden when inactive) so the
// auto-highlight effect keeps tinting a freshly-loaded result regardless of the
// visible tab.
export const SystemsTab: React.FC = () => {
  const s = useCellBuilderStore();
  const [addSlug, setAddSlug] = React.useState<string | null>(null);
  const [highlighted, setHighlighted] = React.useState(false);
  // A "Procedural model" panel link (clicking a routed run's system) sets
  // focusedSystemName — the parent switches to this tab; scroll+highlight it.
  const focused = s.focusedSystemName;
  const focusedRowRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (focused && focusedRowRef.current)
      focusedRowRef.current.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const equipmentNames = Object.values(s.cells)
    .filter((c) => c.kind === "equipment")
    .map((c) => c.name);
  const systems = Object.values(s.systems);

  // Per-system colour highlighting is ON by default: whenever a compiled result
  // is shown (its draw-ranges resolve once the model tree is built), auto-tint
  // each system with its own colour. Done once per loaded result (tracked by
  // source name) so a manual Revert sticks; reset when the result is unloaded
  // (back to the topology view) so the next result re-highlights.
  const treeData = useTreeViewStore((st) => st.treeData);
  const activeResultSrc = s.detailSourceName ?? s.resultSourceName ?? null;
  const autoHighlightedSrc = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!activeResultSrc) {
      if (highlighted) {
        revertSystemHighlight();
        setHighlighted(false);
      }
      autoHighlightedSrc.current = null;
      return;
    }
    if (autoHighlightedSrc.current === activeResultSrc || systems.length === 0)
      return;
    const n = highlightSystems(systems.map((sys) => sys.name));
    if (n > 0) {
      autoHighlightedSrc.current = activeResultSrc;
      setHighlighted(true);
    }
  }, [activeResultSrc, treeData, systems.length, highlighted]);
  const effectiveSlug = addSlug ?? s.systemTypes[0]?.slug ?? null;
  const selectedAdd =
    s.systemTypes.find((t) => t.slug === effectiveSlug) ?? null;

  return (
    <div className="flex flex-col gap-1.5">
      {systems.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          <button
            className={
              "px-1.5 py-0.5 rounded-sm text-white " +
              (highlighted
                ? "bg-emerald-600 hover:bg-emerald-500"
                : "bg-surface-2 hover:bg-surface-3")
            }
            title="Tint each system's routed geometry with its own colour (dims the rest). Needs a compiled model."
            onClick={() => {
              const n = highlightSystems(systems.map((sys) => sys.name));
              setHighlighted(n > 0);
            }}
          >
            Highlight systems
          </button>
          <button
            className="px-1.5 py-0.5 rounded-sm bg-surface-2 text-content hover:bg-surface-3 disabled:opacity-40"
            disabled={!highlighted}
            title="Restore the original geometry colours"
            onClick={() => {
              revertSystemHighlight();
              setHighlighted(false);
            }}
          >
            Revert
          </button>
        </div>
      )}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-content">add</span>
        <select
          className={inputCls}
          value={effectiveSlug ?? ""}
          onChange={(e) => setAddSlug(e.target.value || null)}
          title="System type — built-in kinds ∪ this scope's DB templates"
        >
          {s.systemTypes.length === 0 && <option value="">no types</option>}
          {s.systemTypes.map((t) => (
            <option key={t.slug} value={t.slug}>
              {t.name} ({t.origin === "code" ? "code" : "db"})
            </option>
          ))}
        </select>
        <button
          className="px-1.5 py-0.5 rounded-sm bg-surface-2 text-content hover:bg-surface-3 disabled:opacity-40"
          disabled={!selectedAdd}
          onClick={() =>
            selectedAdd &&
            s.addSystem(
              selectedAdd.type,
              selectedAdd.origin === "catalog"
                ? { name: selectedAdd.name, medium: selectedAdd.medium }
                : undefined,
            )
          }
        >
          +add
        </button>
        {selectedAdd?.origin === "code" && (
          <button
            className="px-1 rounded-sm text-sky-300 hover:bg-surface-3"
            title="Sync this built-in system kind into the scope's DB catalog"
            onClick={() => void s.syncSystemTypeToDb(selectedAdd.slug)}
          >
            ⤓DB
          </button>
        )}
      </div>
      {systems.length === 0 && (
        <p className="italic text-content-subtle">
          No systems. Add one to route a run between equipment ports.
        </p>
      )}
      {systems.map((sys) => (
        <div
          key={sys.id}
          ref={sys.name === focused ? focusedRowRef : undefined}
          className={
            "rounded-sm p-1 flex flex-col gap-1 border " +
            (sys.name === focused
              ? "border-accent ring-1 ring-accent"
              : "border-edge")
          }
        >
          <div className="flex items-center gap-1">
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              title={`${sys.type} · unique system colour`}
              style={{ background: systemColorHex(sys.name) }}
            />
            <input
              className={`${inputCls} flex-1 min-w-0`}
              value={sys.name}
              onChange={(e) => s.updateSystem(sys.id, { name: e.target.value })}
            />
            <select
              className={inputCls}
              value={sys.type}
              onChange={(e) =>
                s.updateSystem(sys.id, {
                  type: e.target.value as typeof sys.type,
                })
              }
            >
              {(["piping", "duct", "cable", "electrical"] as const).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              className="px-1 rounded-sm hover:bg-surface-3"
              title="Delete system"
              onClick={() => s.removeSystem(sys.id)}
            >
              🗑
            </button>
          </div>
          {sys.connections.map((c, i) => (
            <div key={i} className="flex items-center gap-1 pl-3">
              <span className="text-content-muted">→</span>
              {c.site ? (
                <span
                  className="truncate"
                  title={`Site terminal at ${(c.position ?? [0, 0, 0]).join(", ")}, facing ${(c.directionVector ?? [0, 0, 1]).join(", ")}`}
                >
                  ⌗ {c.site}{" "}
                  <span className="text-content">
                    (site {c.direction}
                    {c.directionVector
                      ? ` ${orientLabel(c.directionVector)}`
                      : ""}
                    )
                  </span>
                </span>
              ) : (
                <span className="truncate">
                  {c.equipment}.<span className="text-content">{c.port}</span>
                </span>
              )}
              <button
                className="ml-auto px-1 rounded-sm hover:bg-surface-3"
                title="Remove connection"
                onClick={() => s.removeSystemConnection(sys.id, i)}
              >
                ✕
              </button>
            </div>
          ))}
          <ConnectionAdder
            equipmentNames={equipmentNames}
            onAdd={(conn) => s.addSystemConnection(sys.id, conn)}
          />
        </div>
      ))}
    </div>
  );
};
