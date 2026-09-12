import React from "react";
import type {UploadFailures} from "./useCorpusFiles";

// Failed-uploads overview + retry. Not portaled — like the copy-from-scope
// modal it renders inside the admin panel's stacking context, so z-50
// suffices here. Retry re-runs only the failed files (the skip-existing
// pre-check makes a partially-succeeded upload a safe no-op).
const UploadFailuresDialog: React.FC<{
    uploadFailures: UploadFailures;
    uploading: boolean;
    onClose: () => void;
    onRetry: (files: File[], folder?: string) => void;
}> = ({uploadFailures, uploading, onClose, onRetry}) => (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-md w-full max-w-lg max-h-[70vh] flex flex-col">
                        <div className="px-4 py-3 border-b border-gray-700 text-sm font-semibold text-gray-100">
                            {uploadFailures.failed.length} upload{uploadFailures.failed.length === 1 ? "" : "s"} failed
                            {uploadFailures.folder ? (
                                <span className="text-gray-400 font-normal"> → {uploadFailures.folder}/</span>
                            ) : null}
                        </div>
                        <div className="flex-1 min-h-0 overflow-auto px-4 py-2">
                            <ul className="space-y-1 text-xs">
                                {uploadFailures.failed.map(({file, reason}) => (
                                    <li key={file.name} className="flex justify-between items-baseline gap-3">
                                        <span className="font-mono text-gray-200 truncate" title={file.name}>
                                            {file.name}
                                        </span>
                                        <span className="text-red-400 truncate shrink-0 max-w-[50%]" title={reason}>
                                            {reason}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div className="px-4 py-3 border-t border-gray-700 flex justify-end gap-2">
                            <button
                                type="button"
                                onClick={onClose}
                                className="text-sm px-3 py-1 rounded-sm text-gray-300 hover:bg-gray-800"
                            >
                                Close
                            </button>
                            <button
                                type="button"
                                disabled={!!uploading}
                                onClick={() => {
                                    const {folder, failed} = uploadFailures;
                                    onRetry(failed.map((f) => f.file), folder);
                                }}
                                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                            >
                                Retry {uploadFailures.failed.length} file{uploadFailures.failed.length === 1 ? "" : "s"}
                            </button>
                        </div>
                    </div>
                </div>
);

export default UploadFailuresDialog;
