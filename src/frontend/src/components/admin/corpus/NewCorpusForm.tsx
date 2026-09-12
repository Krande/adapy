import React, {useCallback, useState} from "react";
import {viewerApi} from "@/services/viewerApi";
import {SLUG_RE} from "./shared";

const NewCorpusForm: React.FC<{onCreated: () => void}> = ({onCreated}) => {
    const [slug, setSlug] = useState("");
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const onSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        setErr(null);
        if (!SLUG_RE.test(slug)) {
            setErr("slug must be lowercase ASCII with hyphen separators (e.g. cad-baseline)");
            return;
        }
        if (!name.trim()) {
            setErr("name required");
            return;
        }
        setBusy(true);
        try {
            await viewerApi.adminCorpusCreate({
                slug, name: name.trim(),
                description: description.trim() || null,
            });
            setSlug("");
            setName("");
            setDescription("");
            onCreated();
        } catch (e) {
            setErr((e as Error).message || "create failed");
        } finally {
            setBusy(false);
        }
    }, [slug, name, description, onCreated]);

    return (
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Slug</span>
                <input
                    type="text"
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="cad-baseline"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Name</span>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="CAD baseline"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 w-48"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1 flex-1 min-w-[180px]">
                <span>Description <span className="text-gray-500">(optional)</span></span>
                <input
                    type="text"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Representative STEP / IFC files for release-gate sweeps"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                />
            </label>
            <button
                type="submit"
                disabled={busy}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Creating…" : "Create corpus"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">{err}</div>
            )}
        </form>
    );
};

export default NewCorpusForm;
