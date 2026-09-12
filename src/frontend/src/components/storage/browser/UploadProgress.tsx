import React from "react";
import {formatBytes} from "@/utils/format";

// In-flight move status line and the upload progress bar.
const UploadProgress: React.FC<{
    opNote: string | null;
    uploadName: string | null;
    uploadLoaded: number;
    uploadTotal: number;
}> = ({opNote, uploadName, uploadLoaded, uploadTotal}) => (
    <>
            {opNote && (
                <div className="mb-2 flex items-center gap-2 text-xs text-blue-300">
                    <span
                        className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0"
                        aria-hidden="true"
                    />
                    <span className="truncate flex-1 min-w-0" role="status">{opNote}</span>
                </div>
            )}
            {uploadName && (
                <div className="mb-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                        <span className="truncate flex-1 min-w-0" title={uploadName}>
                            Uploading {uploadName}
                        </span>
                        <span className="shrink-0 tabular-nums">
                            {uploadTotal > 0
                                ? `${formatBytes(uploadLoaded)} / ${formatBytes(uploadTotal)}`
                                : formatBytes(uploadLoaded)}
                        </span>
                    </div>
                    <div className="mt-1 h-1 w-full bg-gray-700 rounded-sm overflow-hidden">
                        {uploadTotal > 0 ? (
                            <div
                                className="h-full bg-blue-600 transition-[width] duration-200"
                                style={{
                                    width: `${Math.max(
                                        0,
                                        Math.min(100, Math.round((uploadLoaded / uploadTotal) * 100)),
                                    )}%`,
                                }}
                            />
                        ) : (
                            <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                </div>
            )}
    </>
);

export default UploadProgress;
