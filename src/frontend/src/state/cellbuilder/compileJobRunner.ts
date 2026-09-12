/**
 * The procedural BUILD DRIVER shared by compile and preview.
 *
 * Owns: announcing a build on the global toast, enqueuing it, polling the job
 * to completion, and on success fetching the engine log + quantity take-off,
 * announcing the result to a follower tab and showing it in whichever view is
 * active. Bound to the store once (set/get) and handed to the jobs slice, so
 * compile() and compilePreview() differ only in what they enqueue.
 */

import {capabilities} from "@/services/capabilities";
import {useStatsStore} from "@/state/statsStore";
import {postPreviewReady} from "@/utils/cellbuilder/proceduralChannel";
import type {CellBuilderGet, CellBuilderSet} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {modelMaxX} from "./bounds";

export function makeStartCompileJob(set: CellBuilderSet, get: CellBuilderGet) {
  // Drive a procedural build (a committed compile OR an ephemeral preview) to
  // completion: announce the toast, enqueue, then poll job status and auto-show
  // the result when ready. Shared by compile() and compilePreview() so the two
  // paths behave identically apart from what they enqueue.
  const startCompileJob = async (
    label: string,
    lod: "sim" | "detail",
    enqueue: () => Promise<{
      job_id: string | null;
      derived_key: string;
      cached?: boolean;
    }>,
  ): Promise<void> => {
    setProceduralToast(label, {
      status: "queued",
      stage: "queued",
      progress: 0,
      startedAt: Date.now(),
    });
    // Clear any prior log while this build runs; it's refetched on completion.
    set({ compileLog: null, compileLogRunId: null, compileLogIsCurrentRun: false });
    // Fetch the engine-compile log for a finished (or failed) build and stash it
    // so the panel's "Compile log" section can show the engine's messages. Best
    // effort — a missing log resolves to "" and never blocks the result.
    const fetchCompileLog = async (derivedKey: string, runId?: string | null) => {
      const active = get().active;
      if (!active || (!derivedKey && !runId)) return;
      try {
        const res = await capabilities.procedural.fetchCompileLog(
          currentScopePart(),
          active.modelId,
          derivedKey,
          runId,
        );
        set({
          compileLog: res.text,
          compileLogRunId: res.runId || null,
          compileLogIsCurrentRun: Boolean(runId) && res.runId === runId,
        });
      } catch {
        // Leave compileLog as-is; the log is a diagnostic, not load-bearing.
      }
    };
    // Fetch the quantity take-off computed alongside the build (Stats panel).
    // Best effort — a model without a sidecar degrades to "not available".
    const fetchStats = (derivedKey: string) => {
      const active = get().active;
      if (!active || !derivedKey) return;
      void useStatsStore
        .getState()
        .fetchModelStats(currentScopePart(), active.modelId, derivedKey)
        .then(() => {
          // The take-off carries the per-type joint counts (from the detailing
          // stage). Surface them as the Detailing tab's "N detected" badges; a
          // model with no joints clears the badges.
          const joints = useStatsStore.getState().stats?.joints;
          const counts = joints?.by_type?.length
            ? Object.fromEntries(joints.by_type.map((t) => [t.slug, t.count]))
            : null;
          set({ detailingJointCounts: counts });
        });
    };
    // Announce a ready server build to any follower tab (BroadcastChannel), so a
    // second window opened with ?pfollow=<modelId> can load and show it live.
    const broadcast = (derivedKey: string) => {
      const active = get().active;
      if (!active || !derivedKey) return;
      postPreviewReady({
        modelId: active.modelId,
        scope: currentScopePart(),
        derivedKey,
        lod,
        name: active.name,
      });
    };
    // Show the freshly-built result — WITHOUT moving the camera (viewResult loads
    // with autoFit off) and WITHOUT superimposing it on the topology. Topology and
    // result are separate layers: the result renders only when it's the thing to
    // show — either side-by-side is on (result sits beside the topology) or a
    // matching result view is active. In plain Topology view it stays hidden, so
    // the topology view shows only topology. Only the LOD the current view wants
    // renders, so building "both" never double-draws.
    const autoShow = () => {
      const cur = get().compileJob;
      if (!cur || !cur.derivedKey) return;
      const rm = lod === "detail" ? "detail" : "simulation";
      if (get().sideBySide) {
        // Result sits BESIDE the topology (left = topology, right = result). Only
        // render the LOD the current view wants, so building "both" doesn't stack
        // two results on the right; leave repMode alone (topology stays on left).
        // Compare in the LOD vocabulary ("sim"/"detail"), NOT repMode's
        // ("simulation"/"detail") — otherwise a sim build ("sim") never matched
        // "simulation" and the result silently never refreshed beside topology.
        const wantLod = get().repMode === "detail" ? "detail" : "sim";
        if (lod === wantLod) void get().viewResult(cur.derivedKey, lod);
        return;
      }
      // Not side-by-side: this result IS the view. Switch to it from Topology (or
      // refresh it if already there) — result-only (no superimpose), no camera
      // move. Don't yank the user out of a DIFFERENT result view they're reading.
      if (get().repMode === "topology" || get().repMode === rm) {
        if (get().repMode !== rm) {
          set({ repMode: rm });
          get().setCellsVisible(get().superimpose);
          if (rm === "simulation") get().hideDetail();
          else get().hideResult();
        }
        void get().viewResult(cur.derivedKey, lod);
      }
    };
    try {
      const res = await enqueue();
      if (res.cached) {
        set({
          compileJob: { jobId: null, derivedKey: res.derived_key, status: "cached" },
        });
        setProceduralToast(label, {
          status: "done",
          progress: 1,
          stage: "cached",
          derivedKey: res.derived_key,
        });
        autoShow();
        broadcast(res.derived_key);
        void fetchCompileLog(res.derived_key, null);
        fetchStats(res.derived_key);
        return;
      }
      set({
        compileJob: { jobId: res.job_id, derivedKey: res.derived_key, status: "queued" },
      });
      setProceduralToast(label, {
        status: "queued",
        stage: "queued",
        jobId: res.job_id ?? "",
        derivedKey: res.derived_key,
      });
      const jobId = res.job_id!;
      const poll = async () => {
        const cur = get().compileJob;
        if (!cur || cur.jobId !== jobId) return; // superseded
        try {
          const st = await capabilities.procedural.jobStatus(jobId);
          if (st.status === "done") {
            set({ compileJob: { ...cur, status: "done" } });
            setProceduralToast(label, {
              status: "done",
              progress: 1,
              stage: st.stage || "ready",
              derivedKey: st.derived_key || cur.derivedKey || "",
            });
            autoShow();
            broadcast(st.derived_key || cur.derivedKey || "");
            void fetchCompileLog(st.derived_key || cur.derivedKey || "", jobId);
            fetchStats(st.derived_key || cur.derivedKey || "");
            return;
          }
          if (st.status === "error") {
            set({
              compileJob: { ...cur, status: "error", error: st.error ?? "compile failed" },
            });
            setProceduralToast(label, {
              status: "error",
              stage: st.stage || "",
              error: st.error ?? "compile failed",
            });
            // A failed compile still persists its log (errors are inspectable).
            void fetchCompileLog(cur.derivedKey || "", jobId);
            return;
          }
          set({ compileJob: { ...cur, status: "running" } });
          setProceduralToast(label, {
            status: "running",
            progress: st.progress ?? 0,
            stage: st.stage || "",
            jobId,
          });
          setTimeout(poll, 1500);
        } catch (e) {
          const error = e instanceof Error ? e.message : String(e);
          set({ compileJob: { ...cur, status: "error", error } });
          setProceduralToast(label, { status: "error", error });
        }
      };
      setTimeout(poll, 1500);
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      set({ compileJob: { jobId: null, derivedKey: "", status: "error", error } });
      setProceduralToast(label, { status: "error", error });
    }
  };

  return startCompileJob;
}
