/**
 * The VIEW tab.
 *
 * Owns: the three-way representation switch (topology / simulation / detail), the
 * superimpose and side-by-side modifiers, the follower-tab link, the cell-layer
 * and per-cell hide controls, the port overlay toggle and the type-icon overlay.
 */

import React from "react";

import {runtime} from "@/runtime/config";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {followerUrl} from "@/utils/cellbuilder/proceduralChannel";
import {Section, btn, btnGray, isReadOnly} from "./chrome";
import {IconOverlaySection} from "./IconOverlaySection";

export const ViewTab: React.FC = () => {
  const s = useCellBuilderStore();
  const readOnly = isReadOnly(s);
  const compileState = s.compileJob;
  const compileBusy =
    compileState != null &&
        (compileState.status === "queued" || compileState.status === "running");
  return (
    <>
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-gray-400 mr-1">Representation</span>
        <span
          className="inline-flex rounded-sm overflow-hidden text-[11px]"
          role="group"
          aria-label="Model representation"
        >
          {(
            [
              [
                "topology",
                "Topology",
                "The editable cell model (boxes + equipment)",
              ],
              [
                "simulation",
                "Simulation",
                "The compiled analysis result (plates, beams, systems)",
              ],
              [
                "detail",
                "Detail",
                "The high-fidelity detail model (trimmed deck edges, I-girder joints)",
              ],
            ] as const
          ).map(([m, label, title], idx) => (
            <button
              key={m}
              className={
                "px-2 py-0.5 " +
                (s.repMode === m
                  ? "bg-blue-600 text-white"
                  : "bg-gray-700 text-gray-300 hover:bg-gray-600")
              }
              onClick={() => void s.setRepMode(m)}
              aria-pressed={s.repMode === m}
              title={`${title} — ⇧${idx + 1} jumps here; \` cycles views, ⇧\` reverse`}
            >
              <span className="opacity-50 mr-0.5">⇧{idx + 1}</span>
              {label}
              {s.repMode === m && m !== "topology" && compileBusy
                ? " …"
                : ""}
            </button>
          ))}
        </span>
      </div>
      <label
        className="inline-flex items-center gap-1 text-gray-300 cursor-pointer"
        title="Keep the editable topology cells visible underneath the compiled result (result superimposed on topology)"
      >
        <input
          type="checkbox"
          className="accent-blue-600"
          checked={s.superimpose}
          onChange={(e) => void s.setSuperimpose(e.target.checked)}
        />
        Superimpose topology under result
      </label>
      <label
        className="inline-flex items-center gap-1 text-gray-300 cursor-pointer"
        title="Show the compiled result BESIDE the editable topology (offset to the right) instead of on top of it. Edit the topology on the left and watch the result update on the right — ⇧↵ recompiles a preview without committing."
      >
        <input
          type="checkbox"
          className="accent-blue-600"
          checked={s.sideBySide}
          onChange={(e) => s.setSideBySide(e.target.checked)}
        />
        Side-by-side (result beside topology)
      </label>
      {/* Needs `active.modelId` (the follower fetches that model's builds), so
          it is gated on `readOnly` as well as on the file:// origin -- the
          non-null assertion below is only safe because both keep it
          unclickable when there is no session. */}
      <button
        className={btnGray + " self-start"}
        disabled={readOnly || runtime.isFileOrigin()}
        onClick={() => {
          const scope = useScopeStore.getState().current;
          const scopePart = scope ? scopeUrlPart(scope) : "user:me";
          window.open(
            followerUrl(s.active!.modelId, scopePart),
            "_blank",
            "noopener",
          );
        }}
        title={
          readOnly
            ? "Needs an editable model — a follower window follows the builds of a model opened from storage."
            : runtime.isFileOrigin()
              ? "Not available in a locally opened file — a follower window needs a real server origin to share (BroadcastChannel and window.open() both require one)."
              : "Open a second window that shows this model's compiled result and updates live as you edit here (⇧↵ recompiles a preview). Best across two screens."
        }
      >
        Open result in new window
      </button>

      <button
        className={btnGray + " self-start"}
        onClick={() => s.recenterModel()}
        title="Recompute the model's placement from the current cells so it sits centered in the scene. Use this after deleting a far-off cell/equipment that had skewed the centering."
      >
        Recenter model in scene
      </button>

      <div className="flex items-center gap-3 flex-wrap pt-1 border-t border-gray-600/40">
        <span className="text-gray-400">Compile builds</span>
        <label
          className="inline-flex items-center gap-1 cursor-pointer"
          title="Produce the simulation-level result (plates, beams, systems)"
        >
          <input
            type="checkbox"
            className="accent-blue-600"
            checked={s.buildSim}
            onChange={(e) => s.setBuildSim(e.target.checked)}
          />
          Simulation
        </label>
        <label
          className="inline-flex items-center gap-1 cursor-pointer"
          title="Also produce the high-fidelity detail result (trimmed deck edges, I-girder joints). Switch the representation to Detail to view it."
        >
          <input
            type="checkbox"
            className="accent-blue-600"
            checked={s.buildDetail}
            onChange={(e) => s.setBuildDetail(e.target.checked)}
          />
          Detail
        </label>
      </div>

      <Section title="Overlays">
        <IconOverlaySection />
        <button
          className={
            (s.portsOverlayVisible ? btn : btnGray) + " self-start"
          }
          onClick={() => s.setPortsOverlayVisible(!s.portsOverlayVisible)}
          title="Toggle the port overlay: each equipment's input/output positions and vectors — plus site I/O terminals — drawn as coloured arrows (colours match the equipment catalog)"
          aria-pressed={s.portsOverlayVisible}
        >
          {s.portsOverlayVisible ? "Hide ports" : "Show ports"}
        </button>
      </Section>
    </>
  );
};
