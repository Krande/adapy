// Small DTOs shared by more than one domain module (files.ts and
// adminStorage.ts both list/rename/move blobs; both shapes travel through
// the same wire format). Kept out of either module so neither has to import
// the other just for a type.

import type { TargetFormat } from "../client";

/** One successfully renamed/moved source key with its derived-sibling tally. */
export interface MovedKeyEntry {
  old: string;
  new: string;
  siblings_moved: number;
  siblings_failed: string[];
}

export interface MoveKeysResult {
  moved: MovedKeyEntry[];
  failed: Array<{ key: string; reason: string }>;
}

/** Present, and "uploading", while the key's presigned upload hasn't
 * completed (server-side: pending_uploads.py) — a listing row in this state
 * has no confirmed bytes behind it yet, whatever `size` says. `size` is a
 * best-effort 0 until either the client's own size hint or a heartbeat says
 * otherwise; `upload_progress` is absent until at least one heartbeat has
 * landed. */
export interface UploadingFields {
  status: "uploading";
  upload_progress?: { loaded: number; total: number; updated_at: number | null };
}

export interface FileEntry extends Partial<UploadingFields> {
  key: string;
  size: number;
}

export interface DerivedBlob {
  format: string;
  key: string;
  size: number;
  last_modified: string | null;
}

export interface AdminFileEntry extends Partial<UploadingFields> {
  key: string;
  size: number;
  last_modified: string | null;
  format: string;
  available_targets: TargetFormat[];
  derived: DerivedBlob[];
  orphan?: boolean;
}
