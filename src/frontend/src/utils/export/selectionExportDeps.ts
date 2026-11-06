// The real wiring of a selection export (`SelectionExportDeps`): the route, the job status, the
// download and the progress toast, every file written and named with the export options as they
// are when it is sent. Shared by the object info panel's "Download as…" and the File menu's
// Export dialog, so both write the same files.

import {conversionApi} from "@/services/api/conversion";
import {filesApi} from "@/services/api/files";
import {selectionExportApi} from "@/services/api/selectionExport";
import {trackJob} from "@/services/jobTracking";
import type {ScopeUrl} from "@/services/viewerApi";
import {exportOptionsWire, useExportOptionsStore} from "@/state/exportOptionsStore";
import {exportFileName} from "@/utils/exportFileName";

import type {SelectionExportDeps} from "./selectionExport";

/** `project`: the one collection the exported members share, for the file name and the IFC header. */
export function selectionExportDeps(scope: ScopeUrl, project: string | null): SelectionExportDeps {
    return {
        api: {
            exportSelection: (s, body) => {
                const o = useExportOptionsStore.getState();
                const labelled = body as {format?: string; element?: string | null; label?: string | null};
                // The IFC header names the file as it will be saved, and the project it is from; the
                // server adds the signed-in user as its author.
                const fileName = exportFileName({
                    serverName: `${labelled.element || labelled.label || "selection"}.${labelled.format ?? "ifc"}`,
                    project: o.prefixProject ? project : null,
                    customPrefix: o.customPrefix,
                    includeName: o.includeName,
                });
                return selectionExportApi.exportSelection(s, {
                    ...body,
                    export_options: {
                        ...exportOptionsWire(o),
                        ifc_header: {file_name: fileName, ...(project ? {project} : {})},
                    },
                });
            },
            async jobStatus(jobId) {
                const status = await conversionApi.convertStatus(jobId);
                return {status: status.status, error: status.error};
            },
        },
        // Saved under the configured prefixes, read when the file arrives.
        download: (s, key, filename) => {
            const o = useExportOptionsStore.getState();
            return filesApi.downloadBlob(
                s,
                key,
                exportFileName({
                    serverName: filename,
                    project: o.prefixProject ? project : null,
                    customPrefix: o.customPrefix,
                    includeName: o.includeName,
                }),
            );
        },
        trackJob: (opts) => void trackJob({...opts, scopeUrl: scope}),
    };
}
