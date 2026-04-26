import React from "react";
import {useServerInfoStore, ServerFileEntry} from "../../state/serverInfoStore";
import {request_list_of_files_from_server} from "../../utils/server_info/comms/request_list_of_files_from_server";
import {view_file_object_from_server} from "../../utils/scene/comms/view_file_object_from_server";
import {FileObjectT, FileObject} from "../../flatbuffers/base/file-object";
import * as flatbuffers from "flatbuffers";
import ReloadIcon from "../icons/ReloadIcon";
import ViewIcon from "../icons/ViewIcon";

const apiBase = () => ((window as any).API_BASE || "/api").replace(/\/+$/, "");

function downloadOriginal(name: string) {
    const url = `${apiBase()}/blobs/${encodeURIComponent(name)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function buildFlatbufferFileObject(entry: ServerFileEntry): FileObject {
    // The view helper expects an actual flatbuffer FileObject. We
    // synthesize one from the stored entry so the existing call site
    // works unchanged.
    const builder = new flatbuffers.Builder(256);
    const t = new FileObjectT(entry.name, entry.fileType, undefined, entry.filepath || entry.name);
    const offset = t.pack(builder);
    builder.finish(offset);
    return FileObject.getRootAsFileObject(builder.dataBuffer());
}

const StorageBrowser: React.FC = () => {
    const files = useServerInfoStore((s) => s.serverFileObjects);

    const onView = (entry: ServerFileEntry) => {
        view_file_object_from_server(buildFlatbufferFileObject(entry));
    };

    return (
        <div
            data-no-upload-menu
            className="bg-gray-800/95 text-gray-100 rounded p-2 mt-2 ml-2 mr-2 min-w-80 max-w-md border border-gray-700"
        >
            <div className="flex justify-between items-center mb-2">
                <h2 className="font-bold">Storage</h2>
                <button
                    className="bg-blue-700 hover:bg-blue-600 text-white p-1 rounded"
                    onClick={() => request_list_of_files_from_server()}
                    title="Refresh list"
                >
                    <ReloadIcon/>
                </button>
            </div>
            {files.length === 0 ? (
                <div className="text-xs text-gray-400 italic">
                    No files yet. Right-click in the viewer to upload.
                </div>
            ) : (
                <ul className="flex flex-col gap-1 max-h-80 overflow-auto">
                    {files.map((f) => (
                        <li
                            key={f.name}
                            className="flex items-center justify-between bg-gray-900/60 rounded px-2 py-1 text-xs"
                        >
                            <span className="truncate flex-1 mr-2" title={f.name}>{f.name}</span>
                            <div className="flex items-center gap-1 shrink-0">
                                <button
                                    className="p-1 rounded hover:bg-gray-700"
                                    onClick={() => onView(f)}
                                    title="View"
                                >
                                    <ViewIcon/>
                                </button>
                                <button
                                    className="px-2 py-0.5 rounded hover:bg-gray-700 text-[10px] uppercase tracking-wide text-gray-300"
                                    onClick={() => downloadOriginal(f.name)}
                                    title="Download original"
                                >
                                    DL
                                </button>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default StorageBrowser;
