import React, {useRef, useState} from "react";
import {uploadFile} from "@/utils/scene/handlers/upload_source_file";

// The picker modal drives the move flows and the upload-destination prompt;
// ``onPick`` is the closure that knows what to do once a destination is chosen.
export interface FolderPicker {
    title: string;
    allowRoot?: boolean;
    submitLabel?: string;
    onPick: (folder: string) => Promise<void> | void;
}

// Uploads: the owned file inputs, per-file progress, and the batch uploader
// used by the "+" menu, "Upload here…" and OS-file drops.
export function useUploads(p: {setPicker: (picker: FolderPicker | null) => void}) {
    const {setPicker} = p;
    const [uploading, setUploading] = useState(false);
    // Upload progress: name = current file (or null), loaded/total in
    // bytes. Total may stay 0 if the browser can't determine it (rare
    // for File uploads); we treat that as indeterminate.
    const [uploadName, setUploadName] = useState<string | null>(null);
    const [uploadLoaded, setUploadLoaded] = useState(0);
    const [uploadTotal, setUploadTotal] = useState(0);

    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);
    // Hidden picker for "Import from Excel…" in the + menu — imports create a
    // NEW procedural model, so the entry point lives here rather than in the
    // cellbuilder panel (which only exists once a model is open).
    const importXlsxInputRef = useRef<HTMLInputElement>(null);
    // Folder destination for the next picker-initiated upload
    // ("Upload here…" on a folder). Consumed once by onFilePicked.
    const uploadTargetRef = useRef<string | null>(null);

    // Upload a batch sequentially (presigned PUT is per-file); a failed
    // file is collected and reported at the end rather than aborting
    // the batch. ``folder`` prefixes every file's key — used by
    // "Upload here…" and OS-file drops onto a folder row.
    const uploadFilesTo = async (list: File[], folder?: string) => {
        if (list.length === 0) return;
        setUploading(true);
        const failures: string[] = [];
        for (let i = 0; i < list.length; i++) {
            const file = list[i];
            setUploadName(list.length > 1 ? `${file.name} (${i + 1}/${list.length})` : file.name);
            setUploadLoaded(0);
            setUploadTotal(file.size);
            try {
                await uploadFile(file, {
                    folder,
                    onProgress: (loaded, total) => {
                        setUploadLoaded(loaded);
                        if (total) setUploadTotal(total);
                    },
                });
            } catch (err) {
                console.error("upload failed", file.name, err);
                failures.push(file.name);
            }
        }
        setUploading(false);
        setUploadName(null);
        setUploadLoaded(0);
        setUploadTotal(0);
        if (failures.length) window.alert(`Upload failed for: ${failures.join(", ")}`);
    };

    const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        e.target.value = "";
        const folder = uploadTargetRef.current;
        uploadTargetRef.current = null;
        if (picked.length === 0) return;
        if (folder !== null) {
            // "Upload here…" on a folder row — destination already chosen.
            void uploadFilesTo(picked, folder || undefined);
            return;
        }
        // Generic "Upload files…": ask where the batch should land — an
        // existing folder, a new path, or the top level (the default).
        setPicker({
            title: `Upload ${picked.length} file${picked.length === 1 ? "" : "s"} to`,
            allowRoot: true,
            submitLabel: "Upload",
            onPick: (dest) => void uploadFilesTo(picked, dest || undefined),
        });
    };

    return {
        uploading,
        uploadName,
        uploadLoaded,
        uploadTotal,
        fileInputRef,
        importXlsxInputRef,
        uploadTargetRef,
        uploadFilesTo,
        onFilePicked,
    };
}
