// What the Simulations panel lists in the active mode (`ModeBehaviour.simulations`), and the mode
// a run's result opens in (`SimulationProviderUi.mode`). Pure: the panel hands in the mode and how
// to read a provider's declared mode.

import type { ModeBehaviour, ModeId } from "@/layout/modes";

/** The active mode as far as the panel needs it; null in a UI without modes. */
export interface SimulationsModeView {
  id: ModeId;
  behaviour?: ModeBehaviour;
}

/** A provider as the filter reads it. */
export interface ScopedProvider {
  id: string;
  domain: string;
}

/** The panel's filter in `mode`; null where it lists everything, in the server's order. */
export function simulationsFilterFor(mode: SimulationsModeView | null): { mode: ModeId; domain?: string; empty?: string } | null {
  const s = mode?.behaviour?.simulations;
  if (!mode || !s) return null;
  return { mode: mode.id, domain: s.domain, empty: s.empty };
}

/** Is `provider` listed in `mode`: of the mode's domain, when it names one. No provider is hidden for its declared mode. */
export function providerShownInMode(provider: ScopedProvider, mode: SimulationsModeView | null): boolean {
  const f = simulationsFilterFor(mode);
  return !f?.domain || provider.domain === f.domain;
}

/**
 * `providers` with the ones whose plugin UI declares `mode` first (`modeOf` reads that, or null),
 * each part in its own order. Unchanged without a filter.
 */
export function modeProvidersFirst<P extends ScopedProvider>(
  providers: readonly P[],
  mode: SimulationsModeView | null,
  modeOf: (provider: P) => ModeId | null,
): P[] {
  const f = simulationsFilterFor(mode);
  if (!f) return [...providers];
  const own = providers.filter((p) => modeOf(p) === f.mode);
  return [...own, ...providers.filter((p) => !own.includes(p))];
}

/** Does any of `providers` declare `mode`: false is when the panel says the mode's `empty`. */
export function modeHasOwnProvider<P extends ScopedProvider>(
  providers: readonly P[],
  mode: SimulationsModeView | null,
  modeOf: (provider: P) => ModeId | null,
): boolean {
  const f = simulationsFilterFor(mode);
  return !f || providers.some((p) => modeOf(p) === f.mode);
}

/**
 * The mode to switch to before a run's result opens: the provider's declared mode, when the UI on
 * screen offers it and it is not already the active one; else null (stay).
 */
export function modeToOpenIn(
  declared: ModeId | null,
  active: ModeId | null,
  offered: (id: ModeId) => boolean,
): ModeId | null {
  if (!declared || declared === active || !offered(declared)) return null;
  return declared;
}
