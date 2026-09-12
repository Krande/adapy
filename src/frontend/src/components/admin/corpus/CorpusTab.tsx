import React, {useCallback, useEffect, useMemo, useState} from "react";
import {Corpus, viewerApi} from "@/services/viewerApi";
import NewCorpusForm from "./NewCorpusForm";
import CorpusList from "./CorpusList";
import CorpusFiles from "./CorpusFiles";

// Admin tab — manage proprietary regression corpora (M3 of the audit
// panel design in the admin audit-panel design notes).
//
// Each corpus is its own scope (``corpus:<slug>``). RBAC is admin-only on
// every axis: scope_can_access rejects non-admin reads on the backend.
//
// The trigger form on the audit Runs sub-tab picks a corpus by slug from
// the same /admin/corpora list this tab maintains.

const CorpusTab: React.FC = () => {
    const [corpora, setCorpora] = useState<Corpus[]>([]);
    const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
    const [listError, setListError] = useState<string | null>(null);

    const loadCorpora = useCallback(async () => {
        try {
            const r = await viewerApi.adminCorporaList();
            setCorpora(r.corpora);
            setListError(null);
        } catch (e) {
            setListError((e as Error).message || "failed to load corpora");
        }
    }, []);

    useEffect(() => { void loadCorpora(); }, [loadCorpora]);

    const selected = useMemo(
        () => corpora.find((c) => c.slug === selectedSlug) || null,
        [corpora, selectedSlug],
    );

    const onArchive = useCallback(async (slug: string) => {
        if (!confirm(
            `Archive ${slug}? Storage bytes survive — wipe those separately ` +
            `if you need the space back. The slug can be reused right away.`,
        )) return;
        try {
            await viewerApi.adminCorpusArchive(slug);
            if (selectedSlug === slug) setSelectedSlug(null);
            await loadCorpora();
        } catch (e) {
            setListError((e as Error).message || "archive failed");
        }
    }, [selectedSlug, loadCorpora]);

    const showList = !selectedSlug;

    return (
        <div className="flex flex-col h-full">
            <NewCorpusForm onCreated={loadCorpora}/>

            <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
                <CorpusList
                    corpora={corpora}
                    selectedSlug={selectedSlug}
                    onSelect={setSelectedSlug}
                    onArchive={onArchive}
                    visible={showList}
                    listError={listError}
                />

                {/* Per-corpus files — hidden on mobile when no corpus
                    is selected. */}
                <div className={
                    "flex-1 min-h-0 flex-col overflow-hidden " +
                    (showList ? "hidden md:flex" : "flex")
                }>
                    {!selected && (
                        <div className="hidden md:block text-xs text-gray-500 italic px-4 py-6">
                            Pick a corpus from the list to manage its files.
                        </div>
                    )}
                    {selected && (
                        <>
                            <div className="md:hidden px-3 py-2 border-b border-gray-800">
                                <button
                                    type="button"
                                    onClick={() => setSelectedSlug(null)}
                                    className="text-sm text-blue-400 hover:text-blue-300"
                                >
                                    ← corpora
                                </button>
                            </div>
                            <CorpusFiles key={selected.slug} corpus={selected} onMetaUpdated={loadCorpora}/>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CorpusTab;
