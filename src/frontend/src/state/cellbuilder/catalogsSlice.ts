/**
 * Cellbuilder CATALOGS slice.
 *
 * Owns: the engine-advertised option lists the pickers read — equipment, cell
 * and opening types, system types, design rulesets and procedural engines —
 * which of each is selected, and the catalog write-backs (sync one archetype,
 * resync them all). The lists are fetched through the capability seam, so they
 * arrive identically over REST and the websocket.
 */

import {capabilities} from "@/services/capabilities";
import type {ProceduralCellTypeOption, ProceduralDesignRulesetOption, ProceduralEngineSummary, ProceduralOpeningTypeOption, ProceduralSystemTypeOption, ProceduralTypeOption} from "@/services/viewerApi";
import type {ResyncSummary} from "./types";
import type {CellBuilderSlice} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {makeWithHistory} from "./historySlice";

export interface CatalogsSlice {
  /** Equipment types for the add-equipment dropdown: code archetypes ∪ the
   * per-scope DB catalog, each tagged with its origin. */
  equipmentTypes: ProceduralTypeOption[];
  selectedEquipmentType: string | null; // a slug
  /** Space-cell types for the + Cell picker: built-in blueprints ∪ engine-
   * advertised, each with a default size + metadata. */
  cellTypes: ProceduralCellTypeOption[];
  selectedCellType: string | null; // a slug
  /** Opening types for the + Opening picker: built-in door/window ∪ engine-
   * advertised, each with a subtype + default size. */
  openingTypes: ProceduralOpeningTypeOption[];
  selectedOpeningType: string | null; // a slug
  /** System types for the systems inspector: code kinds ∪ DB templates. */
  systemTypes: ProceduralSystemTypeOption[];
  /** Named design ruleset slug (routing/penetration rules); round-trips as
   * doc.design_rules and is resolved to callables by the compiler. */
  designRules: string;
  /** Available design rulesets for the ruleset dropdown (code ∪ worker). */
  designRulesets: ProceduralDesignRulesetOption[];
  /** Selected procedural engine slug (compile-time only, not part of the model
   * document). "adapy-default" = the built-in compile. */
  selectedEngine: string;
  /** Available procedural engines for the engine dropdown (built-ins ∪ DB). */
  engines: ProceduralEngineSummary[];
  setDesignRules: (slug: string) => void;
  setSelectedEngine: (slug: string) => void;
  fetchEngines: () => Promise<void>;
  setSelectedEquipmentType: (t: string | null) => void;
  setSelectedCellType: (t: string | null) => void;
  setSelectedOpeningType: (t: string | null) => void;
  /** Step the active cell type through the advertised catalog (keyboard T). */
  cycleCellType: (dir: 1 | -1) => void;
  /** Step the active equipment type through the advertised catalog (keyboard
   * equipment-insert mode). */
  cycleEquipmentType: (dir: 1 | -1) => void;
  fetchEquipmentTypes: () => Promise<void>;
  fetchCellTypes: () => Promise<void>;
  fetchOpeningTypes: () => Promise<void>;
  fetchSystemTypes: () => Promise<void>;
  fetchDesignRulesets: () => Promise<void>;
  /** Persist a code-origin type into the scope's DB catalog, then refresh. */
  syncEquipmentTypeToDb: (slug: string) => Promise<void>;
  syncSystemTypeToDb: (slug: string) => Promise<void>;
  /** Upsert ALL code equipment archetypes into the catalog, updating existing
   * entries so code changes (new ports, corrected heights) reach placed
   * equipment. ``quiet`` suppresses the toast when nothing changed (auto-resync
   * on model open). Returns the per-slug outcome, or null on failure. */
  resyncEquipmentTypes: (opts?: { quiet?: boolean }) => Promise<{
    created: string[];
    updated: string[];
    unchanged: string[];
    skipped: string[];
    changes: Record<string, string[]>;
  } | null>;
  /** True while a (non-quiet) resync is in flight, so the button can disable. */
  resyncBusy: boolean;
  /** Result of the last user-triggered resync, shown as a summary popup listing
   * which equipment changed and how. Null when dismissed / never run. */
  resyncSummary: ResyncSummary | null;
  dismissResyncSummary: () => void;
}

export const createCatalogsSlice: CellBuilderSlice<CatalogsSlice> = (set, get) => {
  const withHistory = makeWithHistory(set);

  return {
    equipmentTypes: [],
    selectedEquipmentType: null,
    cellTypes: [],
    selectedCellType: null,
    openingTypes: [],
    selectedOpeningType: null,
    systemTypes: [],
    resyncBusy: false,
    resyncSummary: null,
    designRules: "standard",
    designRulesets: [],
    selectedEngine: "adapy-default",
    engines: [],
    setDesignRules: (designRules) =>
      withHistory(() => ({ designRules, dirty: true })),
    setSelectedEquipmentType: (selectedEquipmentType) =>
      set({ selectedEquipmentType }),
    setSelectedCellType: (selectedCellType) => set({ selectedCellType }),
    setSelectedOpeningType: (selectedOpeningType) =>
      set({ selectedOpeningType }),
    cycleCellType: (dir) =>
      set((s) => {
        if (!s.cellTypes.length) return {};
        const slugs = s.cellTypes.map((t) => t.slug);
        const i = slugs.indexOf(s.selectedCellType ?? "");
        const ni = (((i < 0 ? 0 : i) + dir) % slugs.length + slugs.length) %
          slugs.length;
        return { selectedCellType: slugs[ni] };
      }),
    cycleEquipmentType: (dir) =>
      set((s) => {
        if (!s.equipmentTypes.length) return {};
        const slugs = s.equipmentTypes.map((t) => t.slug);
        const i = slugs.indexOf(s.selectedEquipmentType ?? "");
        const ni = (((i < 0 ? 0 : i) + dir) % slugs.length + slugs.length) %
          slugs.length;
        return { selectedEquipmentType: slugs[ni] };
      }),
    fetchEquipmentTypes: async () => {
      try {
        const types = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "equipmentTypes",
        );
        set((s) => ({
          equipmentTypes: types,
          selectedEquipmentType:
            s.selectedEquipmentType &&
            types.some((t) => t.slug === s.selectedEquipmentType)
              ? s.selectedEquipmentType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: equipment-types fetch failed", e);
        set({ equipmentTypes: [] });
      }
    },

    fetchCellTypes: async () => {
      try {
        const types = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "cellTypes",
        );
        set((s) => ({
          cellTypes: types,
          selectedCellType:
            s.selectedCellType &&
            types.some((t) => t.slug === s.selectedCellType)
              ? s.selectedCellType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: cell-types fetch failed", e);
        set({ cellTypes: [] });
      }
    },

    fetchOpeningTypes: async () => {
      try {
        const types = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "openingTypes",
        );
        set((s) => ({
          openingTypes: types,
          selectedOpeningType:
            s.selectedOpeningType &&
            types.some((t) => t.slug === s.selectedOpeningType)
              ? s.selectedOpeningType
              : (types[0]?.slug ?? null),
        }));
      } catch (e) {
        console.warn("cellbuilder: opening-types fetch failed", e);
        set({ openingTypes: [] });
      }
    },

    fetchSystemTypes: async () => {
      try {
        const types = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "systemTypes",
        );
        set({ systemTypes: types });
      } catch (e) {
        console.warn("cellbuilder: system-types fetch failed", e);
        set({ systemTypes: [] });
      }
    },

    fetchDesignRulesets: async () => {
      try {
        const rulesets = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "designRulesets",
        );
        set({ designRulesets: rulesets });
      } catch (e) {
        console.warn("cellbuilder: design-rulesets fetch failed", e);
        set({ designRulesets: [] });
      }
    },

    // Engine selection is a compile-time choice, not part of the model document
    // — no history / no doc round-trip; just picks which engine the next compile
    // dispatches to (server and in-browser both resolve it identically). The
    // offered BLUEPRINTS are engine-scoped, so a change refetches them and
    // reconciles the selection (to the new engine's default if the current one
    // isn't offered).
    setSelectedEngine: (slug) => {
      set({ selectedEngine: slug || "adapy-default" });
      void get().fetchBlueprints();
    },

    fetchEngines: async () => {
      try {
        const engines = await capabilities.procedural.listCatalog(
          currentScopePart(),
          "engines",
        );
        set({ engines });
      } catch (e) {
        console.warn("cellbuilder: engines fetch failed", e);
        set({ engines: [] });
      }
    },

    syncEquipmentTypeToDb: async (slug) => {
      try {
        await capabilities.procedural.syncCatalogEntry(
          currentScopePart(),
          "equipmentTypes",
          slug,
        );
        await get().fetchEquipmentTypes();
      } catch (e) {
        console.warn("cellbuilder: equipment-type sync failed", e);
      }
    },

    syncSystemTypeToDb: async (slug) => {
      try {
        await capabilities.procedural.syncCatalogEntry(
          currentScopePart(),
          "systemTypes",
          slug,
        );
        await get().fetchSystemTypes();
      } catch (e) {
        console.warn("cellbuilder: system-type sync failed", e);
      }
    },

    resyncEquipmentTypes: async (opts) => {
      const quiet = opts?.quiet ?? false;
      if (!quiet) {
        if (get().resyncBusy) return null; // already running (button also disables)
        set({ resyncBusy: true, resyncSummary: null });
        // Show the toast immediately (the upsert is quick but the round-trip isn't
        // instant) so the click gives feedback instead of appearing to do nothing.
        setProceduralToast("Resync equipments", {
          status: "running",
          progress: 0,
          stage: "syncing catalog…",
          startedAt: Date.now(),
        });
      }
      try {
        const res = await capabilities.procedural.resyncEquipmentTypes(
          currentScopePart(),
        );
        await get().fetchEquipmentTypes();
        const changed = res.created.length + res.updated.length;
        // Announce on the global toast unless this was a silent auto-resync that
        // found nothing to change (avoid noise on every model open).
        if (!quiet || changed > 0) {
          setProceduralToast("Resync equipments", {
            status: "done",
            progress: 1,
            stage: changed
              ? `${res.updated.length} updated, ${res.created.length} added`
              : "catalog already up to date",
          });
        }
        // A user-triggered resync also opens a summary popup detailing which
        // equipment changed and how (a quiet auto-resync stays silent).
        if (!quiet) set({ resyncSummary: res });
        return res;
      } catch (e) {
        console.warn("cellbuilder: equipment resync failed", e);
        if (!quiet)
          setProceduralToast("Resync equipments", {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        return null;
      } finally {
        if (!quiet) set({ resyncBusy: false });
      }
    },

    dismissResyncSummary: () => set({ resyncSummary: null }),
  };
};
