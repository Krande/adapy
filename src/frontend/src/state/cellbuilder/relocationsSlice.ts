/**
 * Cellbuilder RELOCATIONS slice.
 *
 * Owns: the last set of proposed equipment moves that would clear the model's
 * cramped or unroutable runs, and applying them. Proposing commits first (the
 * worker analyses the committed document) and never moves anything by itself —
 * the user applies, which lands as a single undo step.
 */

import {capabilities} from "@/services/capabilities";
import type {ProceduralRelocationResult} from "@/services/viewerApi";
import {runProceduralIo} from "@/services/compute/proceduralIo";
import type {CellBuilderSlice} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {awaitProceduralJob, equipmentFor, proceduralIoTask, runInBrowser} from "./proceduralIoSupport";

export interface RelocationsSlice {
  /** The last relocation proposals (or null). Populated by proposeRelocations;
   * applied only when the user clicks Apply. */
  relocations: ProceduralRelocationResult | null;
  relocationBusy: boolean;
  /** Analyse the model and propose the minimum equipment moves that clear its
   * cramped/unroutable runs. Commits first (the worker reads the committed doc).
   * Never applies them — sets `relocations` for the panel to show. */
  proposeRelocations: () => Promise<void>;
  /** Apply the current proposals: move each named equipment to its proposed
   * position (converting origin → box corner) and mark dirty. Clears
   * `relocations`. The user then recompiles. */
  applyRelocations: () => void;
}

export const createRelocationsSlice: CellBuilderSlice<RelocationsSlice> = (set, get) => {
  return {
    relocations: null,
    relocationBusy: false,

    proposeRelocations: async () => {
      const s = get();
      if (!s.active || s.relocationBusy) return;
      // The worker analyses the COMMITTED doc from postgres, so flush edits first.
      if (s.dirty) {
        const ok = await get().commit();
        if (!ok) return;
      }
      const active = get().active;
      if (!active) return;
      const label = active.name;
      set({ relocationBusy: true, relocations: null });
      setProceduralToast(`Relocations: ${label}`, {
        status: "queued",
        stage: "analyzing",
        progress: 0,
        startedAt: Date.now(),
      });
      const toast = `Relocations: ${label}`;
      const scope = currentScopePart();
      try {
        // Where the execution policy's "procedural_io" order says: in the browser
        // (core's relocate on the committed document, with the catalog equipment it
        // places), else the worker job, whose proposals are a stored blob.
        const doc = get().toDoc();
        const { value: data } = await runProceduralIo<ProceduralRelocationResult>(
          await proceduralIoTask(get, "propose_relocations", { doc }),
          {
            browser: async () => {
              setProceduralToast(toast, { status: "running", stage: "analyzing in browser" });
              const answer = await runInBrowser("propose_relocations", {
                doc,
                equipment: await equipmentFor(get, doc),
              });
              return JSON.parse(new TextDecoder().decode(answer)) as ProceduralRelocationResult;
            },
            worker: async (explicit) => {
              const res = await capabilities.procedural.proposeRelocations(scope, active.modelId, {
                runOnWorker: explicit,
              });
              let key = res.derived_key;
              if (res.job_id) {
                key =
                  (await awaitProceduralJob(
                    res.job_id,
                    (st) =>
                      setProceduralToast(toast, {
                        status: "running",
                        progress: st.progress ?? 0,
                        stage: st.stage || "analyzing",
                      }),
                    { firstDelayMs: 1500 },
                  )) || res.derived_key;
              }
              return capabilities.procedural.fetchRelocations(scope, key);
            },
          },
        );
        set({ relocations: data, relocationBusy: false });
        setProceduralToast(toast, {
          status: "done",
          progress: 1,
          stage: data.proposals.length ? `${data.proposals.length} proposed` : "no moves needed",
        });
      } catch (e) {
        set({ relocationBusy: false });
        setProceduralToast(toast, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    applyRelocations: () => {
      const proposals = get().relocations?.proposals ?? [];
      if (proposals.length === 0) return;
      const byName = new Map(
        Object.values(get().cells).map((c) => [c.name, c] as const),
      );
      // One undo step for the whole apply.
      get().beginTransaction();
      for (const p of proposals) {
        const cell = byName.get(p.equipment);
        if (!cell || cell.kind !== "equipment") continue;
        // Proposal from/to are equipment ORIGINS (X+LX/2, Y+LY/2, Z); convert
        // back to the box corner the store keeps.
        get().updateCell(cell.id, {
          origin: [
            p.to[0] - cell.size[0] / 2,
            p.to[1] - cell.size[1] / 2,
            p.to[2],
          ],
        });
      }
      get().endTransaction();
      set({ relocations: null });
    },
  };
};
