// "Simulate…" in the connection studio's Connection tab: offer the detail that was built as a
// simulation subject and open the Simulations panel on it.
//
// The studio only OFFERS the subject (state/simulationSubjects); which providers can run on it is the
// panel's business (`accepts`), and where they run -- here, or handed to a remote Ada Studio -- the
// server's. So this file knows a spec, its inputs and the subject store, and nothing about robots.

import { devConnectionsApi, type DevBuildResult, type ConnectionInputs } from "@/services/api/devConnections";
import { useSimulationStore } from "@/state/simulationStore";
import { useSimulationSubjectStore } from "@/state/simulationSubjects";

/** Who offered the subject, as the panel shows it. */
export const SUBJECT_SOURCE = "connection studio";

/** Whether the Simulate button works, and when it does not, what to tell the person. */
export interface SimulateAvailability {
  readonly enabled: boolean;
  readonly reason: string | null;
}

/**
 * Simulate is available when the library is ready and the last SUCCESSFUL build of the selected
 * spec has welds -- the joint on screen is the one that would be offered.
 */
export function simulateAvailability(
  libraryStatus: string | null | undefined,
  selectedSpec: string | null,
  lastResult: Pick<DevBuildResult, "spec_name" | "counts"> | null,
  busy: boolean = false,
): SimulateAvailability {
  if (busy) return { enabled: false, reason: "Offering the detail for simulation…" };
  if (!selectedSpec) return { enabled: false, reason: "Pick a connection first." };
  if (libraryStatus !== "ready") return { enabled: false, reason: "The library is not ready." };
  if (!lastResult || lastResult.spec_name !== selectedSpec) {
    return { enabled: false, reason: "Nothing built for this connection yet: simulate once it has built." };
  }
  if (!((lastResult.counts.welds ?? 0) > 0)) {
    return { enabled: false, reason: "This detail has no welds: there is nothing to simulate." };
  }
  return { enabled: true, reason: null };
}

/** What the picker calls the subject: `"<spec> (<n> welds)"`. */
export function subjectLabel(specName: string, welds: number): string {
  return `${specName} (${welds} weld${welds === 1 ? "" : "s"})`;
}

/**
 * Store the detail built from `specName` + `inputs` as a weld assembly, offer it as a subject and
 * open the Simulations panel. Throws what the route refuses (`DevSubjectRefused`: no welds;
 * `DevBuildError`: the builder raised); the caller shows it.
 */
export async function simulateDetail(specName: string, inputs: ConnectionInputs): Promise<void> {
  const subject = await devConnectionsApi.subject(specName, inputs);
  useSimulationSubjectStore.getState().offer({
    kind: subject.kind,
    key: subject.key,
    scope: subject.scope,
    label: subjectLabel(specName, subject.welds),
    source: SUBJECT_SOURCE,
  });
  const sims = useSimulationStore.getState();
  // The panel is offered once a provider is listed; refresh the list (a handed-off studio lists the
  // remote's), so it is there to open.
  try {
    await sims.loadProviders();
  } catch (err) {
    console.warn("simulations: could not refresh the providers", err);
  }
  sims.setPanelOpen(true);
}
