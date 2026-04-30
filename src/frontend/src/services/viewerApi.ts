// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, auth header, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.

import {runtime} from "@/runtime/config";
import {getAccessToken, isAuthEnabled, refreshAccessToken, signIn} from "@/services/auth/oidc";

export type TargetFormat = "glb" | "ifc" | "xml";
export type ConvertStatus = "queued" | "running" | "done" | "error";

/** Wire-format scope identifier, one of: "shared", "user:me",
 *  "project:<id>". `user:me` is resolved server-side to the caller's
 *  sub so URLs are user-agnostic. */
export type ScopeUrl = string;

export interface MeResponse {
    sub: string;
    email: string;
    displayName: string;
    isAdmin: boolean;
    scopes: Array<{kind: "shared" | "user" | "project"; id: string | null; name: string}>;
    projects: Array<{id: string; slug: string; name: string; role: string}>;
}

export interface FileEntry {
    key: string;
    size: number;
}

export interface ConvertResponse {
    job_id: string;
    source_key: string;
    derived_key: string;
    target_format?: TargetFormat;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    cached: boolean;
    scope_kind?: string;
    scope_id?: string | null;
}

export interface ConvertTargetsResponse {
    source_key: string;
    targets: TargetFormat[];
}

export interface ResultMetaField {
    name: string;
    steps: number[];
}

export interface ResultMeta {
    steps: number[];
    fields: ResultMetaField[];
    default_step: number;
    default_field: string;
}

class ApiError extends Error {
    constructor(message: string, public status: number, public detail?: string) {
        super(message);
        this.name = "ApiError";
    }
}

async function readDetail(r: Response): Promise<string> {
    try {
        return await r.text();
    } catch {
        return "";
    }
}

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
    if (!r.ok) {
        throw new ApiError(`${what} failed: ${r.status} ${r.statusText}`, r.status, await readDetail(r));
    }
    return (await r.json()) as T;
}

function authHeader(): Record<string, string> {
    const t = getAccessToken();
    return t ? {Authorization: `Bearer ${t}`} : {};
}

/**
 * Fetch with auth handling. Attaches the bearer token, and on a 401
 * tries one refresh-then-retry. If still unauthorized, redirects to
 * the IdP — by the time the user comes back, the SPA boots fresh and
 * resumes whatever it was doing.
 *
 * Routes that aren't gated server-side (e.g. /api/config) work
 * regardless because they don't return 401.
 */
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
    const merged: RequestInit = {
        ...init,
        headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
    };
    let r = await fetch(url, merged);
    if (r.status === 401 && isAuthEnabled()) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
            r = await fetch(url, {
                ...init,
                headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
            });
            if (r.status !== 401) return r;
        }
        // No path forward — bounce through the IdP. The current URL
        // is preserved as the post-sign-in return target.
        await signIn(window.location.pathname + window.location.search);
        // signIn navigates away, but if it doesn't (popup blocker?),
        // surface the original 401 so callers don't hang.
    }
    return r;
}

export interface AuditEntry {
    id: number;
    ts: string | null;
    user_sub: string | null;
    scope_kind: string;
    scope_id: string | null;
    action: string;
    key: string | null;
    target_format: string | null;
    status: string | null;
    error: string | null;
    duration_ms: number | null;
    traceback: string | null;
    cpu_user_ms: number | null;
    cpu_sys_ms: number | null;
    peak_rss_kb: number | null;
    read_bytes: number | null;
    write_bytes: number | null;
    profile_key: string | null;
    job_id: string | null;
}

export interface AdminProject {
    id: string;
    slug: string;
    name: string;
    created_at: string | null;
    archived_at: string | null;
    member_count: number;
}

export interface ProjectMember {
    user_sub: string;
    role: string;
    added_at: string | null;
    email: string | null;
    display_name: string | null;
    last_seen_at: string | null;
}

export interface AuditFilters {
    user_sub?: string;
    scope_kind?: string;
    scope_id?: string;
    action?: string;
    before_id?: number;
    limit?: number;
}

export interface DerivedBlob {
    format: string;
    key: string;
    size: number;
    last_modified: string | null;
}

export interface AdminFileEntry {
    key: string;
    size: number;
    last_modified: string | null;
    format: string;
    available_targets: TargetFormat[];
    derived: DerivedBlob[];
    orphan?: boolean;
}

export const viewerApi = {
    /** Direct URL for the addressable blob endpoint. Includes scope.
     * Only safe to use as `<a href download>` when auth is disabled —
     * with auth on use :func:`downloadBlob` so the bearer token rides
     * along. */
    blobUrl(scope: ScopeUrl, key: string): string {
        return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
    },

    /** Bootstrap the SPA's identity + available scopes. */
    async me(): Promise<MeResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/me`);
        return jsonOrThrow<MeResponse>(r, "me");
    },

    async listFiles(scope: ScopeUrl): Promise<FileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files`,
        );
        const body = await jsonOrThrow<{files: FileEntry[]}>(r, `listFiles(${scope})`);
        return body.files;
    },

    /** Trigger a browser download of a stored blob. Fetches with auth,
     * materialises a blob: URL, clicks a hidden anchor, then revokes
     * the URL to release memory. Works in both auth-on and auth-off
     * modes — the only cost over `<a href>` is one extra round-trip
     * the browser would have made anyway. */
    async downloadBlob(scope: ScopeUrl, key: string, suggestedName: string): Promise<void> {
        const r = await authedFetch(this.blobUrl(scope, key));
        if (!r.ok) {
            throw new ApiError(`downloadBlob(${key})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Fetch raw bytes for a key. Used by the in-browser Pyodide
     * pipeline to read its source from storage. */
    async getBlob(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
        const r = await authedFetch(this.blobUrl(scope, key));
        if (!r.ok) {
            throw new ApiError(`getBlob(${key})`, r.status, await readDetail(r));
        }
        return await r.arrayBuffer();
    },

    /** Upload bytes under a given key. body is anything fetch/XHR can
     * send (File, Blob, ArrayBuffer, ...). When `onProgress` is given,
     * the request goes through XMLHttpRequest because fetch doesn't
     * expose upload progress consistently across browsers. */
    async putBlob(
        scope: ScopeUrl,
        key: string,
        body: BodyInit,
        opts?: {onProgress?: (loaded: number, total: number) => void},
    ): Promise<void> {
        if (!opts?.onProgress) {
            const r = await authedFetch(this.blobUrl(scope, key), {
                method: "PUT",
                body,
                headers: {"Content-Type": "application/octet-stream"},
            });
            if (!r.ok) {
                throw new ApiError(`putBlob(${key})`, r.status, await readDetail(r));
            }
            return;
        }

        // Progress-tracked path uses XHR. We don't get authedFetch's
        // refresh-then-retry, but the access token's 30s skew window
        // makes mid-upload expiry vanishingly unlikely.
        await new Promise<void>((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("PUT", this.blobUrl(scope, key));
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            const t = getAccessToken();
            if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
            xhr.upload.addEventListener("progress", (e) => {
                if (e.lengthComputable) {
                    opts.onProgress!(e.loaded, e.total);
                }
            });
            xhr.addEventListener("load", () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve();
                } else {
                    reject(
                        new ApiError(
                            `putBlob(${key}) failed: ${xhr.status}`,
                            xhr.status,
                            xhr.responseText || "",
                        ),
                    );
                }
            });
            xhr.addEventListener("error", () =>
                reject(new ApiError(`putBlob(${key}) network error`, 0, "")),
            );
            xhr.addEventListener("abort", () =>
                reject(new ApiError(`putBlob(${key}) aborted`, 0, "")),
            );
            xhr.send(body as XMLHttpRequestBodyInit);
        });
    },

    /** Request a presigned PUT URL for a too-large-to-buffer upload.
     *
     * Used by uploadFile when the file exceeds the server's regular
     * upload cap (~200 MB). Server returns a one-shot URL the browser
     * PUTs the raw bytes to directly. Local-backed deployments 503
     * here — operator must run with an S3-compatible backend. */
    async requestUploadUrl(
        scope: ScopeUrl,
        key: string,
    ): Promise<{url: string; key: string; method: string; expires_in_seconds: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-url`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key}),
            },
        );
        return jsonOrThrow(r, `requestUploadUrl(${key})`);
    },

    /** Finalise a presigned-URL upload: server confirms the object
     * landed and writes the audit row. Caller should run this only
     * after a successful direct PUT — otherwise it 404s. */
    async completeUpload(scope: ScopeUrl, key: string): Promise<{key: string; size: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-complete`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key}),
            },
        );
        return jsonOrThrow(r, `completeUpload(${key})`);
    },

    /** Inventory of (steps, fields) for a FEA result file. Result is
     * cached server-side after the first parse — calling repeatedly is
     * cheap. 415 if the source isn't a result file; 422 if it is but
     * has no usable result data. */
    async resultMeta(scope: ScopeUrl, sourceKey: string): Promise<ResultMeta> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
                `/result-meta?key=${encodeURIComponent(sourceKey)}`,
        );
        return jsonOrThrow<ResultMeta>(r, `resultMeta(${sourceKey})`);
    },

    /** Enqueue a server-side conversion. Returns either a fresh queued
     * job, a synthesised "cached" response (derived already present),
     * or rejects with ApiError. ``step`` and ``field`` only apply to
     * FEA result sources (.sif) — set both to override the default
     * field selection, or leave both undefined for the auto pick. */
    async convert(
        scope: ScopeUrl,
        sourceKey: string,
        targetFormat: TargetFormat = "glb",
        opts?: {step?: number; field?: string},
    ): Promise<ConvertResponse> {
        const body: Record<string, unknown> = {
            source_key: sourceKey,
            target_format: targetFormat,
        };
        if (opts?.step !== undefined && opts?.field !== undefined) {
            body.step = opts.step;
            body.field = opts.field;
        }
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/convert`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            },
        );
        return jsonOrThrow<ConvertResponse>(r, `convert(${sourceKey} -> ${targetFormat})`);
    },

    /** Poll a single conversion job by id. Job_id is globally unique,
     * so the URL doesn't carry a scope — the server re-checks access
     * against the scope recorded on the job. */
    async convertStatus(jobId: string): Promise<ConvertResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`);
        return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
    },

    /** Server-side viable-target listing. The frontend mirrors this
     * mapping client-side too, but this lets us cross-check. */
    async convertTargets(scope: ScopeUrl, sourceKey: string): Promise<TargetFormat[]> {
        const url =
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
            `/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
        const r = await authedFetch(url);
        if (!r.ok) return [];
        const body = (await r.json()) as ConvertTargetsResponse;
        return body.targets || [];
    },

    /** Admin: paged audit log. ``before_id`` is the keyset cursor —
     * pass ``next_before_id`` from the previous page to get the next
     * older one. Returns null for ``next_before_id`` when at the end. */
    async adminAudit(
        filters: AuditFilters = {},
    ): Promise<{entries: AuditEntry[]; next_before_id: number | null}> {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(filters)) {
            if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
        }
        const qs = params.toString();
        const url = `${runtime.apiBase()}/admin/audit${qs ? `?${qs}` : ""}`;
        const r = await authedFetch(url);
        return jsonOrThrow(r, "adminAudit");
    },

    /** Admin: read a key from app_settings. Value is null when unset. */
    async adminGetSetting(key: string): Promise<string | null> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
        );
        const body = await jsonOrThrow<{key: string; value: string | null}>(r, `adminGetSetting(${key})`);
        return body.value;
    },

    /** Admin: set a key in app_settings. Stringified server-side; the
     * caller is responsible for the encoding (e.g. "true"/"false"). */
    async adminSetSetting(key: string, value: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({value}),
            },
        );
        if (!r.ok) {
            throw new ApiError(`adminSetSetting(${key})`, r.status, await readDetail(r));
        }
    },

    /** Direct URL for a profile-dump download. Auth-aware caller
     * should fetch via authedFetch + blob — exposing the URL here
     * keeps it composable with the table's <a download>. */
    adminProfileUrl(auditId: number): string {
        return `${runtime.apiBase()}/admin/audit/${auditId}/profile`;
    },

    /** Mint a 30-day bearer for CLI / pixi-task use. Returned once;
     * the server does not persist it. */
    async adminMintCliToken(): Promise<{token: string; expires_at: number}> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/auth/cli-token`, {
            method: "POST",
        });
        return jsonOrThrow(r, "adminMintCliToken");
    },

    /** Revoke every previously-minted CLI token for the current user
     * by bumping the per-user cutoff. The OIDC bearer used for this
     * request stays valid. */
    async adminRevokeCliTokens(): Promise<{revoked_at: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/auth/cli-token/revoke`,
            {method: "POST"},
        );
        return jsonOrThrow(r, "adminRevokeCliTokens");
    },

    /** Trigger the original-source download for an audit row. Used by
     * the local repro pixi tasks but also handy for one-off debugging
     * straight from the admin panel. */
    async adminDownloadAuditSource(auditId: number, suggestedName: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/${auditId}/source`,
        );
        if (!r.ok) {
            throw new ApiError(`adminDownloadAuditSource(${auditId})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Trigger the .prof download with the bearer token attached. */
    async adminDownloadProfile(auditId: number, suggestedName: string): Promise<void> {
        const r = await authedFetch(this.adminProfileUrl(auditId));
        if (!r.ok) {
            throw new ApiError(`adminDownloadProfile(${auditId})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Admin: clear all conversion metrics + delete profile blobs.
     * Returns counts so the UI can confirm what was wiped. */
    async adminClearMetrics(): Promise<{rows_cleared: number; profiles_deleted: number; errors: string[]}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/metrics`,
            {method: "DELETE"},
        );
        return jsonOrThrow(r, "adminClearMetrics");
    },

    async adminListProjects(): Promise<AdminProject[]> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/projects`);
        const body = await jsonOrThrow<{projects: AdminProject[]}>(r, "adminListProjects");
        return body.projects;
    },

    async adminCreateProject(slug: string, name: string): Promise<AdminProject> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/projects`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({slug, name}),
        });
        return jsonOrThrow<AdminProject>(r, "adminCreateProject");
    },

    async adminArchiveProject(projectId: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}`,
            {method: "DELETE"},
        );
        if (!r.ok && r.status !== 204) {
            throw new ApiError(`adminArchiveProject failed: ${r.status}`, r.status, await readDetail(r));
        }
    },

    async adminListMembers(projectId: string): Promise<ProjectMember[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
        );
        const body = await jsonOrThrow<{members: ProjectMember[]}>(r, "adminListMembers");
        return body.members;
    },

    async adminAddMember(
        projectId: string,
        userSub: string,
        role: string = "member",
    ): Promise<{user_sub: string; role: string; added: boolean}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({user_sub: userSub, role}),
            },
        );
        return jsonOrThrow(r, "adminAddMember");
    },

    /** Admin: enriched per-scope listing (format, last_modified,
     * derived products). Same scope check as the user-facing /files
     * endpoint — admins still need scope access. */
    async adminListStorage(scope: ScopeUrl): Promise<AdminFileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/files`,
        );
        const body = await jsonOrThrow<{files: AdminFileEntry[]}>(r, "adminListStorage");
        return body.files;
    },

    /** Admin: delete a source (and all its derived blobs) or a single
     * derived blob. Returns the list of keys actually removed. */
    async adminDeleteBlob(
        scope: ScopeUrl,
        key: string,
    ): Promise<{deleted: string[]; errors?: string[]}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`,
            {method: "DELETE"},
        );
        return jsonOrThrow(r, "adminDeleteBlob");
    },

    async adminRemoveMember(projectId: string, userSub: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}` +
                `/members/${encodeURIComponent(userSub)}`,
            {method: "DELETE"},
        );
        if (!r.ok && r.status !== 204) {
            throw new ApiError(`adminRemoveMember failed: ${r.status}`, r.status, await readDetail(r));
        }
    },
};

export {ApiError};
