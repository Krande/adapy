import React from "react";
import { PositionedMenu } from "@/components/common/PositionedMenu";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { typePickerItems } from "@/utils/cellbuilder/ports";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { followerUrl } from "@/utils/cellbuilder/proceduralChannel";
import {
  CompileLogSection,
  IconOverlaySection,
  Section,
  describeToolState,
  btn,
  btnGray,
  inputCls,
} from ".";

// The Tools tab body — snapping, the follower window, import/export, and the compile
// log. Moved verbatim out of CellBuilderPanel.
export const ToolsTab: React.FC = () => {
  const s = useCellBuilderStore();

  return (
    <>
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={btnGray}
              disabled={s.resyncBusy}
              onClick={() => void s.resyncEquipmentTypes()}
              title="Update this scope's equipment catalog from the built-in code archetypes (new ports, corrected nozzle heights). Recompile afterwards to pick up the changes."
            >
              {s.resyncBusy ? "Resyncing…" : "Resync equipments"}
            </button>
            <button
              className={btnGray}
              disabled={s.relocationBusy}
              onClick={() => void s.proposeRelocations()}
              title="Analyse the model and propose the fewest equipment moves that make its cramped / unroutable runs clean. Nothing moves until you click Apply."
            >
              {s.relocationBusy ? "Analyzing…" : "Propose relocations"}
            </button>
          </div>

          {/* ── Excel export ── Import lives in the storage panel's "+" menu
              ("Import from Excel…"), since importing creates a NEW procedural
              model rather than editing the one currently open here. */}
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={btnGray}
              disabled={s.xlsxBusy || !s.active}
              onClick={() => void s.exportToExcel()}
              title="Download the current model as the selected engine's Excel workbook (commits any unsaved edits first). Edit it offline and import it back via the storage panel's + menu."
            >
              {s.xlsxBusy ? "Working…" : "Export to Excel"}
            </button>
            {(s.selectedEngine || "adapy-default") === "adapy-default" && (
              <>
                <button
                  className={btnGray}
                  disabled={s.xlsxBusy || !s.active}
                  onClick={() => void s.exportModel("ifc")}
                  title="Download the DETAIL model as an IFC — beams, plates, joints and equipment, with the clash cuts as IfcRelVoidsElement voids (commits any unsaved edits first)."
                >
                  {s.xlsxBusy ? "Working…" : "Download IFC (detail)"}
                </button>
                <label
                  className="flex items-center gap-1 text-gray-300 text-[11px] cursor-pointer"
                  title="Splice real catalog CAD geometry for equipment in the IFC (off = placeholder boxes). Genie XML always uses the equipment concept type."
                >
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={s.exportIfcCad}
                    onChange={(e) => s.setExportIfcCad(e.target.checked)}
                  />
                  CAD equip
                </label>
                <button
                  className={btnGray}
                  disabled={s.xlsxBusy || !s.active}
                  onClick={() => void s.exportModel("gxml")}
                  title="Download the SIMULATION model as a Genie concept XML (.gxml) for Sesam GeniE (commits any unsaved edits first)."
                >
                  {s.xlsxBusy ? "Working…" : "Download Genie XML (sim)"}
                </button>
              </>
            )}
          </div>

          {s.relocations && (
            <div className="border border-amber-500/50 rounded-sm p-1 text-[12px]">
              {s.relocations.proposals.length === 0 ? (
                <p className="text-gray-300">
                  {s.relocations.baseline_problems > 0
                    ? `No move found; ${s.relocations.unresolved.length} run(s) still unresolvable.`
                    : "Routing is clean — no relocations needed."}
                </p>
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-semibold text-amber-300">
                      {s.relocations.proposals.length} move
                      {s.relocations.proposals.length === 1 ? "" : "s"} proposed
                    </span>
                    <button
                      className={btn}
                      onClick={() => s.applyRelocations()}
                      title="Move the equipment as proposed (undoable), then recompile to route cleanly"
                    >
                      Apply moves
                    </button>
                    <button
                      className={btnGray}
                      onClick={() =>
                        useCellBuilderStore.setState({ relocations: null })
                      }
                    >
                      Dismiss
                    </button>
                  </div>
                  <ul className="flex flex-col gap-0.5">
                    {s.relocations.proposals.map((p) => (
                      <li key={p.equipment} className="text-gray-200 break-all">
                        <span className="text-blue-300">{p.equipment}</span>{" "}
                        {p.from.map((v) => v.toFixed(1)).join(",")} →{" "}
                        {p.to.map((v) => v.toFixed(1)).join(",")}
                        <span className="text-gray-500"> — {p.reason}</span>
                      </li>
                    ))}
                  </ul>
                  {s.relocations.unresolved.length > 0 && (
                    <p className="text-red-400 mt-0.5">
                      still unresolved: {s.relocations.unresolved.join(", ")}
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {s.resyncSummary && (
            <div className="border border-blue-500/50 rounded-sm p-1 text-[12px]">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="font-semibold text-blue-300">
                  Equipment resync
                </span>
                <span className="text-gray-400">
                  {s.resyncSummary.updated.length} updated,{" "}
                  {s.resyncSummary.created.length} added,{" "}
                  {s.resyncSummary.unchanged.length} unchanged
                  {s.resyncSummary.skipped.length > 0
                    ? `, ${s.resyncSummary.skipped.length} skipped`
                    : ""}
                </span>
                <button
                  className={btnGray}
                  onClick={() => s.dismissResyncSummary()}
                >
                  Dismiss
                </button>
              </div>
              {s.resyncSummary.created.length +
                s.resyncSummary.updated.length ===
              0 ? (
                <p className="text-gray-300">
                  Catalog already matched the code archetypes — nothing changed.
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {[...s.resyncSummary.updated, ...s.resyncSummary.created].map(
                    (slug) => (
                      <li key={slug} className="text-gray-200">
                        <span className="text-blue-300">{slug}</span>
                        <span className="text-gray-500">
                          {s.resyncSummary!.created.includes(slug)
                            ? " (new)"
                            : " (updated)"}
                        </span>
                        <ul className="ml-3 list-disc list-inside text-gray-400">
                          {(s.resyncSummary!.changes[slug] ?? []).map((c, i) => (
                            <li key={i} className="break-all">
                              {c}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ),
                  )}
                </ul>
              )}
            </div>
          )}

          <CompileLogSection />
    </>
  );
};
