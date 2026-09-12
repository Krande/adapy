/**
 * Cellbuilder DETAILING slice.
 *
 * Owns: the fabrication-detail engine selection (a COMPILE-time choice, not
 * part of the document), the engines advertised, and the per-joint-type option
 * map reconciled against whichever engine is selected — plus the payload shape
 * a compile call ships. "none" means structural-only.
 */

import {capabilities} from "@/services/capabilities";
import type {DetailingEngineSummary} from "@/services/viewerApi";
import {resolveDetailingOptions, toDetailingOptionsPayload, type DetailingOptions} from "@/utils/cellbuilder/detailingOptions";
import type {CellBuilderSlice} from "./state";
import {currentScopePart} from "./shared";

export interface DetailingSlice {
  /** Selected detailing engine slug (fabrication-detail stage; COMPILE-time only,
   * not part of the document). "none" (default) = structural-only. */
  selectedDetailing: string;
  /** Available detailing engines for the Detailing dropdown (built-ins ∪ worker). */
  detailingEngines: DetailingEngineSummary[];
  /** Select the detailing engine (compile-time; "none" = structural-only). */
  setSelectedDetailing: (slug: string) => void;
  fetchDetailingEngines: () => Promise<void>;
  /** Per-joint-type detailing options (toggle + field values), reconciled against
   * the SELECTED detailing engine's advertised joint_types. Empty for "none". */
  detailingOptions: DetailingOptions;
  /** Optional per-joint-type DETECTED counts from the last detailing compile
   * ({jointSlug: n}); null until a compile reports them. Drives the "Detected
   * joints" readout in the Detailing tab when present. */
  detailingJointCounts: Record<string, number> | null;
  /** Toggle a joint family on/off in the Detailing tab. */
  setDetailingJointEnabled: (jointSlug: string, enabled: boolean) => void;
  /** Set one generated field on a joint family in the Detailing tab. */
  setDetailingField: (
    jointSlug: string,
    fieldName: string,
    value: number | boolean | string,
  ) => void;
  /** The per-joint option map the compile call ships as `detailing_options`
   * (`{slug: {enabled, <field>}}`); null when no detailing engine is selected. */
  detailingOptionsPayload: () => ReturnType<typeof toDetailingOptionsPayload>;
}

export const createDetailingSlice: CellBuilderSlice<DetailingSlice> = (set, get) => {
  return {
    selectedDetailing: "none",
    detailingEngines: [],
    detailingOptions: {},
    detailingJointCounts: null,

    // Detailing selection is a compile-time choice too (not part of the document):
    // it picks the fabrication-detail engine the next compile applies after the
    // structural build. "none" = structural-only (byte-identical to today).
    setSelectedDetailing: (slug) => {
      const next = slug || "none";
      set((s) => {
        // Reconcile the per-joint options against the NEWLY-selected engine's
        // advertised specs (mirror how the blueprint selection reconciles on an
        // engine change): keep still-valid edits, default new joints, drop gone
        // ones. "none" advertises nothing -> empty map.
        const engine = s.detailingEngines.find((e) => e.slug === next);
        return {
          selectedDetailing: next,
          detailingOptions: resolveDetailingOptions(engine, s.detailingOptions),
        };
      });
    },

    fetchDetailingEngines: async () => {
      try {
        const detailingEngines = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "detailingEngines",
        );
        // Re-reconcile the current selection's options against the freshly
        // advertised specs (a worker may advertise more/other joint types than
        // the static fallback the first fetch saw).
        set((s) => {
          const engine = detailingEngines.find(
            (e) => e.slug === s.selectedDetailing,
          );
          return {
            detailingEngines,
            detailingOptions: resolveDetailingOptions(
              engine,
              s.detailingOptions,
            ),
          };
        });
      } catch (e) {
        console.warn("cellbuilder: detailing engines fetch failed", e);
        set({ detailingEngines: [], detailingOptions: {} });
      }
    },

    setDetailingJointEnabled: (jointSlug, enabled) =>
      set((s) => {
        const prev = s.detailingOptions[jointSlug];
        if (!prev || prev.enabled === enabled) return {};
        return {
          detailingOptions: {
            ...s.detailingOptions,
            [jointSlug]: { ...prev, enabled },
          },
        };
      }),

    setDetailingField: (jointSlug, fieldName, value) =>
      set((s) => {
        const prev = s.detailingOptions[jointSlug];
        if (!prev) return {};
        return {
          detailingOptions: {
            ...s.detailingOptions,
            [jointSlug]: {
              ...prev,
              fields: { ...prev.fields, [fieldName]: value },
            },
          },
        };
      }),

    detailingOptionsPayload: () => {
      const s = get();
      if (s.selectedDetailing === "none") return null;
      const engine = s.detailingEngines.find(
        (e) => e.slug === s.selectedDetailing,
      );
      return toDetailingOptionsPayload(engine, s.detailingOptions);
    },
  };
};
