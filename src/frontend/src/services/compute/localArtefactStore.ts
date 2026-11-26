// Browser-local artefact store (OPFS with automatic in-memory fallback) +
// explicit "Publish to Scope" uploader.
//
// Browser-computed plugin outputs (sidecar JSON, derived GLBs, reports)
// land here first so they render immediately — including in read-only scopes —
// without touching the server blob store. Users with scope write permission
// can explicitly publish selected or all local artefacts via
// `publishLocalArtefactsToScope`.

import { runtime } from "@/runtime/config";
import { authedFetch } from "@/services/api/client";
import type { LocalArtefactOutput } from "@/utils/pyodide/pyodide_converter";

export interface LocalArtefactMeta {
  scope: string;
  key: string;
  contentType: string;
  size: number;
  updatedAt: number;
  storage: "opfs" | "memory";
}

export interface LocalArtefactRecord extends LocalArtefactMeta {
  bytes: Uint8Array;
}

export interface PublishResult {
  published: string[];
  errors: Array<{ key: string; error: string }>;
}

const OPFS_ROOT_DIR = "ada-local-artefacts";
const DEFAULT_MAX_MEMORY_BYTES = 256 * 1024 * 1024; // 256 MiB fallback cap

function compositeKey(scope: string, key: string): string {
  return `${scope}::${key}`;
}

function encodeSegment(raw: string): string {
  // Reversible, filesystem-safe filename for OPFS (no slashes or colons).
  return encodeURIComponent(raw).replace(/%/g, "_");
}

async function getOpfsRoot(): Promise<FileSystemDirectoryHandle | null> {
  try {
    const nav = typeof navigator !== "undefined" ? navigator : undefined;
    if (!nav?.storage?.getDirectory) return null;
    const root = await nav.storage.getDirectory();
    return await root.getDirectoryHandle(OPFS_ROOT_DIR, { create: true });
  } catch {
    return null;
  }
}

export class LocalArtefactStore {
  private readonly mem = new Map<string, LocalArtefactRecord>();
  private readonly metaIndex = new Map<string, LocalArtefactMeta>();
  private readonly maxMemoryBytes: number;
  private readonly allowOpfs: boolean;

  constructor(opts?: { maxMemoryBytes?: number; allowOpfs?: boolean }) {
    this.maxMemoryBytes = opts?.maxMemoryBytes ?? DEFAULT_MAX_MEMORY_BYTES;
    this.allowOpfs = opts?.allowOpfs ?? true;
  }

  private evictMemoryIfNeeded(incomingBytes: number): void {
    let total = incomingBytes;
    for (const rec of this.mem.values()) total += rec.bytes.byteLength;
    if (total <= this.maxMemoryBytes) return;
    // Map iteration order is insertion order — evict oldest entries first.
    for (const [k, rec] of this.mem.entries()) {
      if (total <= this.maxMemoryBytes) break;
      total -= rec.bytes.byteLength;
      this.mem.delete(k);
      if (this.metaIndex.get(k)?.storage === "memory") {
        this.metaIndex.delete(k);
      }
    }
  }

  async put(
    scope: string,
    key: string,
    bytes: Uint8Array,
    contentType = "application/octet-stream",
  ): Promise<LocalArtefactMeta> {
    const ck = compositeKey(scope, key);
    const copy = new Uint8Array(bytes);
    const updatedAt = Date.now();

    if (this.allowOpfs) {
      const root = await getOpfsRoot();
      if (root) {
        try {
          const scopeDir = await root.getDirectoryHandle(encodeSegment(scope), { create: true });
          const fileHandle = await scopeDir.getFileHandle(encodeSegment(key), { create: true });
          const writable = await fileHandle.createWritable();
          await writable.write(copy);
          await writable.close();
          const meta: LocalArtefactMeta = {
            scope,
            key,
            contentType,
            size: copy.byteLength,
            updatedAt,
            storage: "opfs",
          };
          this.metaIndex.set(ck, meta);
          this.mem.delete(ck);
          return meta;
        } catch {
          // Fall through to in-memory store (e.g. incognito/iframe quota error).
        }
      }
    }

    this.evictMemoryIfNeeded(copy.byteLength);
    const rec: LocalArtefactRecord = {
      scope,
      key,
      contentType,
      size: copy.byteLength,
      updatedAt,
      storage: "memory",
      bytes: copy,
    };
    this.mem.delete(ck);
    this.mem.set(ck, rec);
    this.metaIndex.set(ck, {
      scope,
      key,
      contentType,
      size: rec.size,
      updatedAt,
      storage: "memory",
    });
    return this.metaIndex.get(ck)!;
  }

  async putMany(scope: string, artefacts: LocalArtefactOutput[]): Promise<LocalArtefactMeta[]> {
    const out: LocalArtefactMeta[] = [];
    for (const a of artefacts) {
      out.push(await this.put(scope, a.key, a.bytes, a.contentType));
    }
    return out;
  }

  async get(scope: string, key: string): Promise<LocalArtefactRecord | null> {
    const ck = compositeKey(scope, key);
    const memRec = this.mem.get(ck);
    if (memRec) return memRec;

    const meta = this.metaIndex.get(ck);
    if (this.allowOpfs && (!meta || meta.storage === "opfs")) {
      const root = await getOpfsRoot();
      if (root) {
        try {
          const scopeDir = await root.getDirectoryHandle(encodeSegment(scope));
          const fileHandle = await scopeDir.getFileHandle(encodeSegment(key));
          const file = await fileHandle.getFile();
          const bytes = new Uint8Array(await file.arrayBuffer());
          return {
            scope,
            key,
            contentType: meta?.contentType ?? "application/octet-stream",
            size: bytes.byteLength,
            updatedAt: meta?.updatedAt ?? file.lastModified,
            storage: "opfs",
            bytes,
          };
        } catch {
          return null;
        }
      }
    }
    return null;
  }

  async has(scope: string, key: string): Promise<boolean> {
    const ck = compositeKey(scope, key);
    if (this.mem.has(ck) || this.metaIndex.has(ck)) return true;
    const rec = await this.get(scope, key);
    return rec !== null;
  }

  list(scope: string, prefix = ""): LocalArtefactMeta[] {
    const out: LocalArtefactMeta[] = [];
    for (const meta of this.metaIndex.values()) {
      if (meta.scope !== scope) continue;
      if (prefix && !meta.key.startsWith(prefix)) continue;
      out.push(meta);
    }
    return out.sort((a, b) => a.key.localeCompare(b.key));
  }

  async delete(scope: string, key: string): Promise<void> {
    const ck = compositeKey(scope, key);
    this.mem.delete(ck);
    this.metaIndex.delete(ck);
    if (this.allowOpfs) {
      const root = await getOpfsRoot();
      if (root) {
        try {
          const scopeDir = await root.getDirectoryHandle(encodeSegment(scope));
          await scopeDir.removeEntry(encodeSegment(key));
        } catch {
          /* already absent */
        }
      }
    }
  }

  async clearScope(scope: string): Promise<void> {
    for (const meta of this.list(scope)) {
      await this.delete(scope, meta.key);
    }
  }

  /** Upload selected local artefacts (or all artefacts in `scope` when `keys` is
   * omitted) to the server scope blob store via `PUT /api/scopes/{scope}/blobs/{key}`. */
  async publishToScope(
    scope: string,
    keys?: string[],
    opts?: {
      apiBase?: string;
      fetchImpl?: typeof fetch;
      onProgress?: (done: number, total: number, key: string) => void;
    },
  ): Promise<PublishResult> {
    const targetKeys = keys && keys.length > 0 ? keys : this.list(scope).map((m) => m.key);
    const apiBase = (opts?.apiBase ?? runtime.apiBase()).replace(/\/+$/, "");
    const doFetch = opts?.fetchImpl ?? authedFetch;
    const published: string[] = [];
    const errors: Array<{ key: string; error: string }> = [];

    for (let i = 0; i < targetKeys.length; i++) {
      const key = targetKeys[i];
      const rec = await this.get(scope, key);
      if (!rec) {
        errors.push({ key, error: "local artefact not found" });
        continue;
      }
      const url = `${apiBase}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
      try {
        const resp = await doFetch(url, {
          method: "PUT",
          headers: { "Content-Type": rec.contentType || "application/octet-stream" },
          body: rec.bytes as BodyInit,
        });
        if (!resp.ok) {
          const detail = await resp.text().catch(() => resp.statusText);
          errors.push({ key, error: `HTTP ${resp.status}: ${detail || resp.statusText}` });
        } else {
          published.push(key);
        }
      } catch (e) {
        errors.push({ key, error: e instanceof Error ? e.message : String(e) });
      }
      opts?.onProgress?.(i + 1, targetKeys.length, key);
    }
    return { published, errors };
  }
}

export const localArtefactStore = new LocalArtefactStore();

