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

/** The panel's filter in `mode`; null where it lists everything. */
export function simulationsFilterFor(
  mode: SimulationsModeView | null,
): { mode: ModeId; domain?: string; ownProviders: boolean; empty?: string } | null {
  const s = mode?.behaviour?.simulations;
  if (!mode || !s) return null;
  return { mode: mode.id, domain: s.domain, ownProviders: !!s.ownProviders, empty: s.empty };
}

/** Is `provider` listed in `mode`. `modeOf` reads the mode its plugin UI declares (or null). */
export function providerShownInMode(
  provider: ScopedProvider,
  mode: SimulationsModeView | null,
  modeOf: (provider: ScopedProvider) => ModeId | null,
): boolean {
  const f = simulationsFilterFor(mode);
  if (!f) return true;
  if (f.domain && provider.domain !== f.domain) return false;
  if (f.ownProviders && modeOf(provider) !== f.mode) return false;
  return true;
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
