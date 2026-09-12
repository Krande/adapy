/**
 * The TOOLS tab.
 *
 * Owns: the catalog resync, relocation proposals, the Excel and CAD/analysis
 * exports, and the engine compile log. Everything here acts on the COMMITTED
 * model, so each export commits first when there are unsaved edits.
 */

import React from "react";

import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {btn, btnGray, isReadOnly} from "./chrome";
import {CompileLogSection} from "./CompileLogSection";

export const ToolsTab: React.FC = () => {
  const s = useCellBuilderStore();
  const readOnly = isReadOnly(s);
  return (
    <>
      <div className="flex items-center gap-1 flex-wrap">
        {/* Resync writes to a per-scope DB catalog. Read-only mode has no such
            catalog to write into -- locally the code archetypes ARE the
            catalog -- so this is hidden rather than disabled: it is not a
            feature awaiting a transport, it is meaningless without a server.
            `supports` covers the same case once `readOnly` alone stops
            doing it (an editable ws session still has no DB catalog). */}
        {!readOnly && capabilities.procedural.supports("resyncEquipmentTypes") && (
          <button
            className={btnGray}
            disabled={s.resyncBusy}
            onClick={() => void s.resyncEquipmentTypes()}
            title="Update this scope's equipment catalog from the built-in code archetypes (new ports, corrected nozzle heights). Recompile afterwards to pick up the changes."
          >
            {s.resyncBusy ? "Resyncing…" : "Resync equipments"}
          </button>
        )}
        <button
          className={btnGray}
          disabled={readOnly || !capabilities.procedural.supports("proposeRelocations") || s.relocationBusy}
          onClick={() => void s.proposeRelocations()}
          title={
            readOnly
              ? "Needs an editable model — relocations are proposed against a model you can then apply them to."
              : !capabilities.procedural.supports("proposeRelocations")
                ? "Relocations are analysed by the cloud worker pool, which this transport does not have."
                : "Analyse the model and propose the fewest equipment moves that make its cramped / unroutable runs clean. Nothing moves until you click Apply."
          }
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
          disabled={s.xlsxBusy || !s.active || !capabilities.procedural.supports("exportModel")}
          onClick={() => void s.exportToExcel()}
          title={
            capabilities.procedural.supports("exportModel")
              ? "Download the current model as the selected engine's Excel workbook (commits any unsaved edits first). Edit it offline and import it back via the storage panel's + menu."
              : "Export is built by the cloud worker pool, which this transport does not have."
          }
        >
          {s.xlsxBusy ? "Working…" : "Export to Excel"}
        </button>
        {(s.selectedEngine || "adapy-default") === "adapy-default" && (
          <>
            <button
              className={btnGray}
              disabled={s.xlsxBusy || !s.active || !capabilities.procedural.supports("exportModel")}
              onClick={() => void s.exportModel("ifc")}
              title={
                capabilities.procedural.supports("exportModel")
                  ? "Download the DETAIL model as an IFC — beams, plates, joints and equipment, with the clash cuts as IfcRelVoidsElement voids (commits any unsaved edits first)."
                  : "Export is built by the cloud worker pool, which this transport does not have."
              }
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
              disabled={s.xlsxBusy || !s.active || !capabilities.procedural.supports("exportModel")}
              onClick={() => void s.exportModel("gxml")}
              title={
                capabilities.procedural.supports("exportModel")
                  ? "Download the SIMULATION model as a Genie concept XML (.gxml) for Sesam GeniE (commits any unsaved edits first)."
                  : "Export is built by the cloud worker pool, which this transport does not have."
              }
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
