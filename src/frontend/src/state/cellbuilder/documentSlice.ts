/**
 * Cellbuilder DOCUMENT slice — the editing SESSION and the document round-trip.
 *
 * Owns: `active` (the stored model an edit can be committed back to), the
 * dirty/committing/conflict flags, opening and closing a model, serialising the
 * live builder state to a `ProceduralDoc` (`toDoc`), loading one for viewing
 * (`loadFromDoc`) and committing a revision. `open` deliberately resets the
 * other slices' session state through the composed `set` — a fresh model must
 * not inherit the last one's history, selection or view.
 */

import {ProceduralCommitConflictError, capabilities} from "@/services/capabilities";
import type {ProceduralDoc} from "@/services/viewerApi";
import {groupToStructureName, normalizeGroups} from "@/utils/cellbuilder/groups";
import type {CellBuilderSlice} from "./state";
import {currentScopePart} from "./shared";
import {cellsFromDoc, containingCellName, groupsFromDoc, loftMembersFromDoc, systemsFromDoc} from "./docCodec";

/** Is a procedural document loaded for VIEWING with no editable session behind
 * it -- the GLB-embedded document on the websocket path (`loadFromDoc`)?
 *
 * Enough to show the equipment/systems browser read-only; never enough to edit
 * or commit. Editability is a separate question answered by
 * `capabilities.procedural.canEdit`, which is false on that transport only
 * because no save verb is implemented over the websocket yet.
 *
 * Derived, not stored: it is exactly "something is loaded and there is no
 * session", and a parallel flag would have to be kept in step with `active`,
 * `cells` and `systems` by every action that touches them. Returns a boolean,
 * so it is safe as a zustand selector. */
export const hasEmbeddedDoc = (s: {
  active: unknown | null;
  cells: Record<string, unknown>;
  systems: Record<string, unknown>;
}): boolean =>
  s.active === null && (Object.keys(s.cells).length > 0 || Object.keys(s.systems).length > 0);

export interface DocumentSlice {
  /** The procedural model open in the builder as an EDITABLE SESSION -- a stored
   * model with an id and a revision that edits can be committed back to. Null on
   * the websocket/desktop path even when a document is loaded for viewing (see
   * `hasEmbeddedDoc`), which is deliberate: `active` is what `CellBuilderController`
   * gates every editing interaction on (gizmos, click-to-place, drag-to-move) and
   * what `setupCameraControlsHandlers` reads before auto-compiling, so it must
   * mean "there is somewhere to save this", not merely "there is something to
   * look at". */
  active: { modelId: string; name: string; revision: number } | null;
  dirty: boolean;
  committing: boolean;
  conflict: string | null;
  open: (
    modelId: string,
    name: string,
    revision: number,
    doc: ProceduralDoc,
  ) => void;
  close: () => void;
  toDoc: () => ProceduralDoc;
  loadFromDoc: (doc: ProceduralDoc) => void;
  commit: () => Promise<boolean>;
}

export const createDocumentSlice: CellBuilderSlice<DocumentSlice> = (set, get) => {
  return {
    active: null,
    dirty: false,
    committing: false,
    conflict: null,
    open: (modelId, name, revision, doc) => {
      // A freshly loaded model starts a new editing session — history resets.
      set({
        // A real session supersedes any view-only document that was loaded
        // before it (`hasEmbeddedDoc` is false the moment `active` is set), so
        // the header stops calling itself read-only.
        active: { modelId, name, revision },
        cells: cellsFromDoc(doc),
        loftMembers: loftMembersFromDoc(doc),
        systems: systemsFromDoc(doc),
        groups: groupsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        // The structural blueprint the model was authored with (a legacy doc
        // without one defaults to steel_stru); reconciled against the engine's
        // offered list by fetchBlueprints below.
        selectedBlueprint: doc.blueprint_name ?? "steel_stru",
        // Reflect the engine this model was built for in the dropdown (a
        // capability-engine example opens on that engine, not the adapy-default default).
        selectedEngine: doc.engine || "adapy-default",
        // Detailing selection now rides on the document (a template can default it
        // to adapy-default); absent = "none" (structural-only). Its per-joint
        // options are reconciled from the advertised specs by fetchDetailingEngines
        // below.
        selectedDetailing: doc.detailing ?? "none",
        detailingOptions: {},
        past: [],
        future: [],
        txDepth: 0,
        mode: "idle",
        selection: null,
        selectedCellIds: [],
        gizmoMode: "none",
        contextMenu: null,
        insertMenu: null,
        portMenu: null,
        portGizmo: null,
        cadPreviewCells: [],
        dirty: false,
        conflict: null,
        compileJob: null,
        compileLog: null,
        compileLogRunId: null,
        compileLogIsCurrentRun: false,
        panelVisible: true,
        hiddenCellIds: [],
        // Reset the VIEW state to a clean topology view. Without this, a session
        // that left off in a result view (repMode "simulation" sets cellsVisible
        // = superimpose||sideBySide, i.e. false) taints the next open: the reopened
        // model shows repMode "topology" but with its cells still hidden (empty
        // view) until a repMode toggle re-runs setCellsVisible(true).
        repMode: "topology",
        cellsVisible: true,
        superimpose: false,
        sideBySide: false,
      });
      // Center the new model from its own cells — resets any translation left
      // over from a previously-open model, so fit-all (Shift+A) and empty-space
      // cell placement use THIS model's bounds, not the last one's.
      get().recenterModel();
      void get().fetchEquipmentTypes();
      void get().fetchCellTypes();
      void get().fetchOpeningTypes();
      void get().fetchSystemTypes();
      // Auto-update the catalog from code on open: if a code archetype changed
      // (new port, corrected height), refresh the scope's synced entries so a
      // recompile uses them. Quiet unless something actually changed.
      void get()
        .resyncEquipmentTypes({ quiet: true })
        .then((res) => {
          if (res && res.updated.length + res.created.length > 0)
            void get().fetchEquipmentTypes();
        });
      void get().fetchDesignRulesets();
      void get().fetchEngines();
      void get().fetchDetailingEngines();
      // Blueprints are engine-scoped; fetch for this model's engine and reconcile
      // the selection loaded above against what the engine actually offers.
      void get().fetchBlueprints();
    },
    close: () => {
      get().hideResult();
      get().hideDetail();
      set({
        active: null,
        cells: {},
        loftMembers: [],
        systems: {},
        past: [],
        future: [],
        txDepth: 0,
        mode: "idle",
        selection: null,
        selectedCellIds: [],
        gizmoMode: "none",
        contextMenu: null,
        insertMenu: null,
        portMenu: null,
        portGizmo: null,
        cadPreviewCells: [],
        dirty: false,
        panelVisible: false,
        compileJob: null,
        compileLog: null,
        compileLogRunId: null,
        compileLogIsCurrentRun: false,
        hiddenCellIds: [],
        // Clean view state on close so it can't taint the next open (see open()).
        repMode: "topology",
        cellsVisible: true,
        superimpose: false,
        sideBySide: false,
      });
    },
    toDoc: () => {
      const cells = get().cells;
      const spaces = Object.values(cells)
        .filter((c) => c.kind === "cell")
        .map((c) => {
          // A grouped cell stamps its group as the space's STRUCTURE_NAME (the pm
          // engine reads it back to route the cell into that group's structure);
          // ungrouped cells omit the key entirely (backward compatible).
          const structureName = groupToStructureName(c.group);
          return {
            INCLUDE: true,
            ...c.params,
            NAME: c.name,
            X: c.origin[0],
            Y: c.origin[1],
            Z: c.origin[2],
            DX: c.size[0],
            DY: c.size[1],
            DZ: c.size[2],
            ...(structureName ? { STRUCTURE_NAME: structureName } : {}),
          };
        });
      const equipments = Object.values(cells)
        .filter((c) => c.kind === "equipment")
        .map((c) => ({
          INCLUDE: true,
          SPACE_NAME: containingCellName(cells, c),
          SPACE_LOC: "ROOF",
          COGx: 0,
          COGy: 0,
          COGz: c.size[2] / 2,
          massDry: 0,
          massCont: 0,
          ...c.params,
          NAME: c.name,
          GLOBAL_COORDS: true,
          DESCRIPTION: c.equipmentType ?? null,
          X: c.origin[0],
          Y: c.origin[1],
          Z: c.origin[2],
          LX: c.size[0],
          LY: c.size[1],
          LZ: c.size[2],
          ROT_X: c.rotation?.[0] ?? 0,
          ROT_Y: c.rotation?.[1] ?? 0,
          ROT_Z: c.rotation?.[2] ?? 0,
        }));
      const openings = Object.values(cells)
        .filter((c) => c.kind === "opening")
        .map((c) => ({
          INCLUDE: true,
          ...c.params,
          NAME: c.name,
          SUBTYPE: c.subtype ?? "door",
          USE_GLOBAL_COORDS: true,
          X: c.origin[0],
          Y: c.origin[1],
          Z: c.origin[2],
          DX: c.size[0],
          DY: c.size[1],
          DZ: c.size[2],
        }));
      const systems = Object.values(get().systems).map((sys) => ({
        NAME: sys.name,
        TYPE: sys.type,
        MEDIUM: sys.medium ?? null,
        CONNECTIONS: sys.connections.map((c) =>
          c.site
            ? {
                SITE: c.site,
                POSITION: c.position ?? [0, 0, 0],
                DIRECTION: c.direction ?? "IN",
                DIRECTION_VECTOR: c.directionVector ?? [0, 0, 1],
              }
            : { EQUIPMENT: c.equipment, PORT: c.port },
        ),
      }));
      // Loft members carry the (Phase 3a) station/placement edits — emit the
      // live array, only when present so box-only docs stay byte-identical
      // (mirrors the backend's conditional dump).
      const loftMembers = get().loftMembers;
      // Cell groups (each with its own blueprint), normalized. Emitted only when
      // present so an ungrouped model's doc stays byte-identical (a
      // grouping-unaware engine ignores the key regardless).
      const groups = normalizeGroups(get().groups);
      return {
        grid: {},
        blueprint: get().blueprintOptions,
        // The selected structural blueprint (kept OUT of the whitelisted
        // `blueprint` options); a legacy/absent selection defaults to steel_stru.
        blueprint_name: get().selectedBlueprint ?? "steel_stru",
        design_rules: get().designRules,
        // Persist the fabrication-detail selection so the model (or a template)
        // carries its detailing intent across open/commit ("none" stays absent to
        // keep a structural-only doc byte-identical).
        ...(get().selectedDetailing && get().selectedDetailing !== "none"
          ? { detailing: get().selectedDetailing }
          : {}),
        equipment_cad: get().equipmentCad,
        spaces,
        equipments,
        systems,
        openings,
        ...(loftMembers.length ? { loft_members: loftMembers } : {}),
        ...(groups.length ? { groups } : {}),
      };
    },
    loadFromDoc: (doc) => {
      const cells = cellsFromDoc(doc);
      const systems = systemsFromDoc(doc);
      // Deliberately does NOT set `active` -- see that field's docstring. The
      // store is populated for reading without claiming there is a session to
      // commit to, so `CellBuilderController`'s editing surface stays off. The
      // populated cells/systems are what let the panel and the menu button
      // appear read-only rather than staying hidden entirely (`hasEmbeddedDoc`
      // derives that; there is no separate flag to keep in step).
      //
      // This is also called with an EMPTY doc to clear the panels when a model
      // with no procedural provenance loads; empty cells and systems make
      // `hasEmbeddedDoc` false again, so no empty browser is left behind.
      set({
        cells,
        loftMembers: loftMembersFromDoc(doc),
        systems,
        groups: groupsFromDoc(doc),
        blueprintOptions: doc.blueprint ?? {},
        equipmentCad: Boolean(doc.equipment_cad),
        designRules: doc.design_rules ?? "standard",
        selectedBlueprint: doc.blueprint_name ?? "steel_stru",
        selectedDetailing: doc.detailing ?? "none",
        past: [],
        future: [],
        txDepth: 0,
        dirty: false,
        selection: null,
      });
    },


    commit: async () => {
      const s = get();
      if (!s.active || s.committing) return false;
      set({ committing: true, conflict: null });
      try {
        const res = await capabilities.procedural.commitModel(
          currentScopePart(),
          s.active.modelId,
          s.toDoc(),
          s.active.revision,
        );
        set({
          active: { ...s.active, revision: res.revision },
          dirty: false,
          committing: false,
        });
        if (get().autoCompile) {
          void get().compile();
        }
        return true;
      } catch (e) {
        if (e instanceof ProceduralCommitConflictError) {
          set({
            committing: false,
            conflict:
              "Commit conflict: the model changed elsewhere. Reload it to pick up the latest revision.",
          });
        } else {
          set({
            committing: false,
            conflict: e instanceof Error ? e.message : String(e),
          });
        }
        return false;
      }
    },
  };
};
