import assert from "node:assert/strict";
import { test } from "node:test";

// `viewerApi` reaches `services/auth/oidc.ts`, which reads `sessionStorage` at
// module load, so the browser globals are stubbed BEFORE the dynamic import below.
// Same shape as jobTracking.test.ts: a bare node process has no DOM, and this
// module's behaviour has nothing to do with one.
const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { viewerApi } = await import("@/services/viewerApi");

// WHAT THIS IS FOR. `viewerApi` is assembled from ~17 domain modules under
// services/api/ (`export const viewerApi = { ...authApi, ...filesApi, ... }`).
// That spread is silent about collisions and silent about a method that a
// future re-split simply forgets to spread in — TypeScript only complains if
// a caller's call site breaks, and plenty of callers reach `viewerApi` through
// a type-erased or dynamic path (out-of-tree plugins, `rest_capabilities.ts`'s
// dynamic import) that a missing method wouldn't surface at compile time.
//
// This pins the full method set as a flat, alphabetised list. A method
// dropped from a domain module's spread, or a name typo'd on the way in,
// shows up here as an ADDED/REMOVED diff instead of a runtime "undefined is
// not a function" three files away from the actual mistake.
const EXPECTED_METHODS = [
  "adminAddMember", "adminArchiveProject", "adminAudit", "adminAuditActive",
  "adminAuditCellHistory", "adminAuditClientMetrics", "adminAuditLogSyncIssue",
  "adminAuditRunCancel", "adminAuditRunCells", "adminAuditRunCreate",
  "adminAuditRunDelete", "adminAuditRunGet", "adminAuditRunReDispatch",
  "adminAuditRunRerunCell", "adminAuditRunSyncIssues", "adminAuditRunValidate",
  "adminAuditRunsList", "adminAuditScheduleArchive", "adminAuditScheduleCreate",
  "adminAuditScheduleFireNow", "adminAuditScheduleUpdate", "adminAuditSchedulesList",
  "adminAuditSummary", "adminCancelJob", "adminClearMetrics", "adminCompressionStatus",
  "adminCopyKeysFromScope", "adminCorporaList", "adminCorpusArchive", "adminCorpusCreate",
  "adminCorpusUpdate", "adminCreateProject", "adminDeleteBlob", "adminDownloadAuditSource",
  "adminDownloadProfile", "adminFrontendLoadHotspots", "adminFrontendLoads",
  "adminGetAuditLog", "adminGetSetting", "adminIssueTargetGet", "adminIssueTargetSet",
  "adminListMembers", "adminListProjects", "adminListStorage", "adminListWorkers",
  "adminMetricsHistory", "adminMintCliToken", "adminMoveKeysToFolder", "adminPerfHotspots",
  "adminPerfReport", "adminPerfThresholdsGet", "adminPerfThresholdsSet", "adminPerfWorkers",
  "adminPluginJobScheduleArchive", "adminPluginJobScheduleCreate",
  "adminPluginJobScheduleRunNow", "adminPluginJobScheduleUpdate",
  "adminPluginJobSchedulesList", "adminProfileStats", "adminProfileUrl",
  "adminProvisionCiBot", "adminPruneWorkers", "adminRemoveMember", "adminRenameKey",
  "adminRenameOrMoveFolder", "adminRenderProfiles", "adminRevokeCiBot",
  "adminRevokeCliTokens", "adminSetSetting", "adminStartCompressionSweep",
  "adminWorkerPackages", "auditLocalCreate", "auditLocalUpdate", "blobUrl", "cancelMyJob",
  "commitProceduralModel", "compileProceduralModel", "completeUpload", "componentsBuild",
  "componentsProfiles", "componentsSpecs", "convert", "convertStatus", "convertTargets",
  "copyEquipmentCadFromScope", "createEquipmentType", "createProceduralEngine",
  "createProceduralModel", "createSystemTemplate", "deleteBlob", "deleteEquipmentType",
  "deleteProceduralEngine", "deleteProceduralModel", "deleteSystemTemplate", "downloadBlob",
  "downloadStatsExport", "exportProceduralModel", "exportProceduralModelXlsx",
  "feaArtefactBlobUrl", "feaArtefactUploadTarget", "feaManifest", "fetchModelStats",
  "fetchProceduralImportResult", "fetchProceduralRelocations", "getBlob", "getBlobRange",
  "getEquipmentType", "getProceduralEngine", "getProceduralModel", "getPublicSetting",
  "getSystemTemplate", "importProceduralModelXlsx", "inferEquipmentBbox",
  "listBackendPlugins", "listDetailingEngines", "listEquipmentTypes", "listFiles",
  "listFilesWithDerived", "listOverlays", "listProceduralEngines", "listProceduralModels",
  "listProceduralTemplates", "listSystemTemplates", "me", "moveKeysToFolder", "myJobs",
  "pluginBase", "pluginJob", "previewProceduralModel", "proceduralBlueprints",
  "proceduralCellTypes", "proceduralCompileLog", "proceduralDesignRulesets",
  "proceduralEquipmentTypes", "proceduralOpeningTypes", "proceduralSystemTypes",
  "proposeProceduralRelocations", "putBlob", "putDerivedBlob", "recordRenderProfile",
  "recordViewLoad", "renameKey", "renameOrMoveFolder", "renameProceduralModel",
  "requestDownloadUrl", "requestUploadUrl", "resolveProceduralEngine", "resultMeta",
  "resyncProceduralEquipmentTypes", "runUtility", "syncProceduralEquipmentType",
  "syncProceduralSystemType", "updateEquipmentType", "updateProceduralEngine",
  "updateSystemTemplate", "uploadEquipmentCad", "uploadFeaArtefacts",
  "uploadProceduralImportXlsx", "uploadProgress",
].sort();

test("viewerApi exposes exactly the pinned method set", () => {
  const actual = Object.keys(viewerApi).sort();
  assert.deepEqual(actual, EXPECTED_METHODS);
});

test("the pinned method set has no accidental duplicates", () => {
  assert.equal(new Set(EXPECTED_METHODS).size, EXPECTED_METHODS.length);
});
