import React, {useMemo, useState} from "react";
import type {AuditEntry} from "@/services/viewerApi";
import {DataTable} from "@/components/common/DataTable";
import AdminTabShell from "../AdminTabShell";
import AuditEntryCards from "./AuditEntryCards";
import DetailsModal from "./DetailsModal";
import MetricsControls from "./MetricsControls";
import {AUDIT_LOG_TD_CLASS, AUDIT_LOG_TH_CLASS, buildAuditLogColumns} from "./columns";
import {useAuditLogEntries} from "./useAuditLogEntries";

// Filterable audit log view. Two layouts:
// * sm:↑ desktop — table with sticky header, fits everything in columns.
// * mobile — collapsible filters + card-per-entry, so a 320px viewport
//   stays readable without horizontal scrolling.
//
// "compile" = one procedural compile RUN (app.py _audit_compile_run); its
// worker attaches the run's log_key, so such a row's Log tab shows the engine
// output for exactly that run.

const AuditLogTab: React.FC = () => {
    const {entries, nextBeforeId, loading, error, setError, reload, loadMore} = useAuditLogEntries();
    const [detailsEntry, setDetailsEntry] = useState<AuditEntry | null>(null);

    const columns = useMemo(
        () => buildAuditLogColumns({onDetails: setDetailsEntry, onChanged: () => void reload()}),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [],
    );

    return (
        <AdminTabShell
            header={false}
            subheader={<MetricsControls onError={setError} onCleared={reload}/>}
            error={error}
            loadingLabel={null}
            footer={
                <div className="border-t border-gray-700 px-3 sm:px-4 py-2 flex items-center gap-3 text-xs">
                    <span className="text-gray-400">{entries.length} rows</span>
                    <button
                        className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-sm disabled:opacity-50"
                        onClick={loadMore}
                        disabled={loading || nextBeforeId == null}
                    >
                        Load more
                    </button>
                    {loading && <span className="text-gray-500">loading…</span>}
                </div>
            }
        >
            {/* Desktop / tablet table. ``table-auto`` instead of ``table-fixed``
                so each column sizes to its content rather than the rigid
                colgroup widths. ``whitespace-nowrap`` on the header cells keeps
                content on one line when the natural width exceeds the floor. */}
            <DataTable
                wrap={false}
                columns={columns}
                rows={entries}
                rowKey={(e) => e.id}
                className="hidden sm:table w-full text-sm min-w-[1260px]"
                stickyHeader
                theadClassName="bg-gray-800"
                headerRowClassName="text-left"
                headerCellClassName={AUDIT_LOG_TH_CLASS}
                cellClassName={AUDIT_LOG_TD_CLASS}
                rowClassName="border-t border-gray-800 hover:bg-gray-800/40"
            />
            <AuditEntryCards entries={entries} onDetails={setDetailsEntry}/>
            {!loading && entries.length === 0 && (
                <div className="px-4 py-8 text-center text-gray-500 text-sm">
                    No matching audit entries.
                </div>
            )}
            {detailsEntry && (
                <DetailsModal entry={detailsEntry} onClose={() => setDetailsEntry(null)}/>
            )}
        </AdminTabShell>
    );
};

export default AuditLogTab;
