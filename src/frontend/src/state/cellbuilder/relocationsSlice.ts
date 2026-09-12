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
import type {CellBuilderSlice} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";

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
      try {
        const res = await capabilities.procedural.proposeRelocations(
          currentScopePart(),
          active.modelId,
        );
        if (!res.job_id) {
          const data = await capabilities.procedural.fetchRelocations(
            currentScopePart(),
            res.derived_key,
          );
          set({ relocations: data, relocationBusy: false });
          setProceduralToast(`Relocations: ${label}`, {
            status: "done",
            progress: 1,
            stage: `${data.proposals.length} proposed`,
          });
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await capabilities.procedural.jobStatus(jobId);
            if (st.status === "done") {
              const data = await capabilities.procedural.fetchRelocations(
                currentScopePart(),
                st.derived_key || res.derived_key,
              );
              set({ relocations: data, relocationBusy: false });
              setProceduralToast(`Relocations: ${label}`, {
                status: "done",
                progress: 1,
                stage: data.proposals.length
                  ? `${data.proposals.length} proposed`
                  : "no moves needed",
              });
              return;
            }
            if (st.status === "error") {
              set({ relocationBusy: false });
              setProceduralToast(`Relocations: ${label}`, {
                status: "error",
                error: st.error ?? "relocation analysis failed",
              });
              return;
            }
            setProceduralToast(`Relocations: ${label}`, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "analyzing",
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ relocationBusy: false });
            setProceduralToast(`Relocations: ${label}`, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1500);
      } catch (e) {
        set({ relocationBusy: false });
        setProceduralToast(`Relocations: ${label}`, {
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
