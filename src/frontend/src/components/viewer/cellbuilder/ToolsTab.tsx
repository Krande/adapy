import React from "react";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { CompileLogSection, btn, btnGray } from ".";

// What the Builder's actions produced: relocation proposals, the equipment-resync
// summary, and the compile log.
//
// It used to be those PLUS the five buttons that trigger them. Export is a split button
// in the Build toolbar now and the two analyses are commands under Tools in the menu bar,
// which leaves this tab holding only things you read — and gives it a subject.
export const ToolsTab: React.FC = () => {
  const s = useCellBuilderStore();

  return (
    <>
          {/* Running is not shown here. Resync and Propose relocations are commands
              under Tools in the menu bar, and Export is a split button in the Build
              toolbar — each of them does something and then leaves, which is what a
              toolbar and a menu are for.

              What is left is everything those actions PRODUCE: a relocation proposal you
              read and then accept, a resync summary, the compile log. This tab is the
              model's output, not its controls, and that is the whole reorganisation. */}
          {(s.resyncBusy || s.relocationBusy) && (
            <p className="text-[12px] text-content-muted">
              {s.resyncBusy ? "Resyncing the equipment catalog…" : "Analysing routing…"}
            </p>
          )}

          {s.xlsxBusy && <p className="text-[12px] text-content-muted">Exporting…</p>}

          {s.relocations && (
            <div className="border border-warn rounded-sm p-1 text-[12px]">
              {s.relocations.proposals.length === 0 ? (
                <p className="text-content">
                  {s.relocations.baseline_problems > 0
                    ? `No move found; ${s.relocations.unresolved.length} run(s) still unresolvable.`
                    : "Routing is clean — no relocations needed."}
                </p>
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-semibold text-warn">
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
                      <li key={p.equipment} className="text-content break-all">
                        <span className="text-accent">{p.equipment}</span>{" "}
                        {p.from.map((v) => v.toFixed(1)).join(",")} →{" "}
                        {p.to.map((v) => v.toFixed(1)).join(",")}
                        <span className="text-content-subtle"> — {p.reason}</span>
                      </li>
                    ))}
                  </ul>
                  {s.relocations.unresolved.length > 0 && (
                    <p className="text-fail mt-0.5">
                      still unresolved: {s.relocations.unresolved.join(", ")}
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {s.resyncSummary && (
            <div className="border border-accent rounded-sm p-1 text-[12px]">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="font-semibold text-accent">
                  Equipment resync
                </span>
                <span className="text-content-muted">
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
                <p className="text-content">
                  Catalog already matched the code archetypes — nothing changed.
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {[...s.resyncSummary.updated, ...s.resyncSummary.created].map(
                    (slug) => (
                      <li key={slug} className="text-content">
                        <span className="text-accent">{slug}</span>
                        <span className="text-content-subtle">
                          {s.resyncSummary!.created.includes(slug)
                            ? " (new)"
                            : " (updated)"}
                        </span>
                        <ul className="ml-3 list-disc list-inside text-content-muted">
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
