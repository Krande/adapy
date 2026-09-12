// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, auth header, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.
//
// The implementation lives in ./api/ as one module per domain (files,
// conversion, fea, procedural, admin*, ...), each importing only
// ./api/client.ts for the shared fetch wrapper/error type. This file
// aggregates them into the single `viewerApi` object every caller (and
// every out-of-tree plugin, via the plugin API's `viewer-core/app.ts`
// re-export) has always imported, and re-exports every DTO type so
// `@/services/viewerApi` keeps working unchanged.

import { authApi } from "./api/auth";
import { filesApi } from "./api/files";
import { conversionApi } from "./api/conversion";
import { feaApi } from "./api/fea";
import { componentsApi } from "./api/components";
import { proceduralApi } from "./api/procedural";
import { proceduralCatalogApi } from "./api/proceduralCatalog";
import { equipmentTypesApi } from "./api/equipmentTypes";
import { systemTemplatesApi } from "./api/systemTemplates";
import { pluginsApi } from "./api/plugins";
import { adminAuditApi } from "./api/adminAudit";
import { adminCorpusApi } from "./api/adminCorpus";
import { adminProjectsApi } from "./api/adminProjects";
import { adminStorageApi } from "./api/adminStorage";
import { workersApi } from "./api/workers";
import { metricsApi } from "./api/metrics";
import { settingsApi } from "./api/settings";
export { ApiError } from "./api/client";

export const viewerApi = {
  ...authApi,
  ...filesApi,
  ...conversionApi,
  ...feaApi,
  ...componentsApi,
  ...proceduralApi,
  ...proceduralCatalogApi,
  ...equipmentTypesApi,
  ...systemTemplatesApi,
  ...pluginsApi,
  ...adminAuditApi,
  ...adminCorpusApi,
  ...adminProjectsApi,
  ...adminStorageApi,
  ...workersApi,
  ...metricsApi,
  ...settingsApi,
};

// ── Re-exported DTO types (unchanged import path for ~90 call sites
// and out-of-tree plugins) ────────────────────────────────────────

export type { TargetFormat, ConvertStatus, ScopeUrl } from "./api/client";
export type {
  MovedKeyEntry,
  MoveKeysResult,
  UploadingFields,
  FileEntry,
  DerivedBlob,
  AdminFileEntry,
} from "./api/types/common";
export type { MeResponse } from "./api/auth";
export type { ConvertResponse, ConvertTargetsResponse } from "./api/conversion";
export type {
  ResultMetaField,
  ResultMeta,
  FeaManifestStep,
  FeaScalarRange,
  FeaFieldCategory,
  FeaManifestFieldPerType,
  FeaManifestField,
  FeaManifest,
  FeaManifestLineage,
  FeaManifestLineageGroup,
  FeaHistoryRegionKind,
  FeaHistoryDomain,
  FeaHistoryRegion,
  FeaHistoryVariable,
  FeaHistoryStep,
  FeaHistorySeries,
  FeaManifestHistory,
} from "./api/fea";
export type {
  ComponentSpecRoleSchema,
  ComponentSpecSchema,
  ComponentSpecManifestEntry,
  ComponentSpecsResponse,
  ComponentsProfilesResponse,
  ComponentBuildPayload,
  ComponentBuildResponse,
} from "./api/components";
export type {
  AuditEntry,
  ConvertMeta,
  CppProfilePhase,
  CppProfileThread,
  CppProfile,
  AuditFilters,
  AuditCongestion,
  AuditSummary,
} from "./api/types/audit";
export type {
  WorkerPackage,
  CompressionSweepState,
  WorkerConversion,
  WorkerUtilitySpec,
  WorkerEntry,
} from "./api/workers";
export type {
  AuditRun,
  AuditCellHistoryRow,
  IssueTargetConfig,
  AuditRunJob,
  AuditSchedule,
} from "./api/adminAudit";
export type {
  PerfCell,
  PerfHotspotRow,
  PerfHotspotsResp,
  PerfReport,
  PerfThresholdsResp,
  ProfileStatsRow,
  ProfileStatsResp,
  MetricsSample,
  MetricsHistoryResp,
} from "./api/metrics";
export type { AdminProject, ProjectMember } from "./api/adminProjects";
export type { Corpus } from "./api/adminCorpus";
export type { PluginJobSchedule, BackendPluginSpec } from "./api/plugins";
export type {
  ProceduralModelSummary,
  ProceduralModelDetail,
  ProceduralTemplate,
  ProceduralDoc,
  ProceduralCompileResponse,
  ProceduralXlsxDetect,
  ProceduralRelocation,
  ProceduralRelocationResult,
} from "./api/procedural";
export type {
  TypeOrigin,
  TypePortSummary,
  ProceduralTypeOption,
  ProceduralSystemTypeOption,
  ProceduralCellTypeOption,
  ProceduralOpeningTypeOption,
  ProceduralDesignRulesetOption,
  ProceduralBlueprintOption,
  ProceduralEngineKind,
  ProceduralEngineDoc,
  ProceduralEngineSummary,
  ProceduralEngineDetail,
  ProceduralEngineResolved,
  DetailingFieldSpec,
  DetailingJointTypeSpec,
  DetailingEngineSummary,
  DetailingOptionsPayload,
} from "./api/proceduralCatalog";
export type {
  PortDirection,
  PortCategory,
  CatalogPort,
  EquipmentTypeDoc,
  EquipmentTypeSummary,
  EquipmentTypeDetail,
} from "./api/equipmentTypes";
export type {
  SystemTemplateType,
  SystemTemplateDoc,
  SystemTemplateSummary,
  SystemTemplateDetail,
} from "./api/systemTemplates";
