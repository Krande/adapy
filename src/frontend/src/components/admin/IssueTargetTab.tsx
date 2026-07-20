import React, {useCallback, useEffect, useState} from "react";
import {IssueTargetConfig, viewerApi} from "@/services/viewerApi";

// Admin tab — configure where the audit-bot publishes failure
// issues (the admin audit-panel design notes).
//
// Token-store model: the actual API token lives in a k8s Secret
// exposed to the API process as an environment variable. The DB
// stores only the env-var NAME — never the token itself. That way
// rotating the token is "rotate the Secret + re-roll the
// deployment" and no plaintext credential ever passes through a UI
// form or sits in app_settings.

const KIND_HINTS: Record<IssueTargetConfig["kind"], string> = {
    disabled: "Disable the bot. No issues will be opened or commented.",
    github: "github.com / GitHub Enterprise. base_url defaults to api.github.com if blank.",
    forgejo: "Forgejo / Gitea. base_url is required, e.g. https://git.example.com/api/v1",
};

const IssueTargetTab: React.FC = () => {
    const [cfg, setCfg] = useState<IssueTargetConfig | null>(null);
    const [loadErr, setLoadErr] = useState<string | null>(null);
    const [saveErr, setSaveErr] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [savedAt, setSavedAt] = useState<number | null>(null);

    // Local editable copy. We don't bind directly to ``cfg`` so the
    // save button has a clear "discard changes" flow.
    const [draft, setDraft] = useState<IssueTargetConfig | null>(null);

    const load = useCallback(async () => {
        try {
            const c = await viewerApi.adminIssueTargetGet();
            setCfg(c);
            setDraft(c);
            setLoadErr(null);
        } catch (e) {
            setLoadErr((e as Error).message || "load failed");
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const save = useCallback(async () => {
        if (!draft) return;
        setBusy(true);
        setSaveErr(null);
        try {
            const next = await viewerApi.adminIssueTargetSet({
                kind: draft.kind,
                repo: draft.repo.trim(),
                base_url: draft.base_url.trim() || undefined,
                token_env_name: draft.token_env_name.trim() || undefined,
            });
            setCfg(next);
            setDraft(next);
            setSavedAt(Date.now());
        } catch (e) {
            setSaveErr((e as Error).message || "save failed");
        } finally {
            setBusy(false);
        }
    }, [draft]);

    if (loadErr) {
        return (
            <div className="text-xs text-red-400 px-3 py-2">{loadErr}</div>
        );
    }
    if (!draft || !cfg) {
        return (
            <div className="text-xs text-gray-500 italic px-3 py-2">Loading…</div>
        );
    }

    const dirty = JSON.stringify(draft) !== JSON.stringify(cfg);
    const isDisabled = draft.kind === "disabled";

    return (
        <div className="flex flex-col h-full overflow-auto">
            <div className="px-4 py-3 max-w-2xl space-y-4">
                <div>
                    <h2 className="text-sm font-semibold text-gray-100">
                        Audit issue target
                    </h2>
                    <p className="text-xs text-gray-400 mt-1">
                        When an audit run finishes, the bot opens (or
                        comments on) issues in the configured forge —
                        one per failure fingerprint, with a dashboard
                        issue summarising all open regressions.
                    </p>
                </div>

                <label className="block text-xs text-gray-300 space-y-1">
                    <span className="block">Forge kind</span>
                    <select
                        value={draft.kind}
                        onChange={(e) => setDraft({
                            ...draft,
                            kind: e.target.value as IssueTargetConfig["kind"],
                        })}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 w-60"
                    >
                        <option value="disabled">disabled (no issues)</option>
                        <option value="github">github</option>
                        <option value="forgejo">forgejo / gitea</option>
                    </select>
                    <div className="text-[11px] text-gray-500">{KIND_HINTS[draft.kind]}</div>
                </label>

                <label className="block text-xs text-gray-300 space-y-1">
                    <span className="block">Repository <span className="text-gray-500">(owner/name)</span></span>
                    <input
                        type="text"
                        value={draft.repo}
                        onChange={(e) => setDraft({...draft, repo: e.target.value})}
                        placeholder="example-owner/audit-regressions"
                        disabled={isDisabled}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-full max-w-md disabled:opacity-50"
                    />
                </label>

                {draft.kind === "forgejo" && (
                    <label className="block text-xs text-gray-300 space-y-1">
                        <span className="block">Base URL <span className="text-gray-500">(forge API root)</span></span>
                        <input
                            type="text"
                            value={draft.base_url}
                            onChange={(e) => setDraft({...draft, base_url: e.target.value})}
                            placeholder="https://git.example.com/api/v1"
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-full max-w-md"
                        />
                    </label>
                )}

                <label className="block text-xs text-gray-300 space-y-1">
                    <span className="block">
                        Token environment variable name
                        <span className="text-gray-500"> (k8s Secret → env)</span>
                    </span>
                    <input
                        type="text"
                        value={draft.token_env_name}
                        onChange={(e) => setDraft({...draft, token_env_name: e.target.value})}
                        placeholder="ADA_AUDIT_GITHUB_TOKEN"
                        disabled={isDisabled}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-full max-w-md disabled:opacity-50"
                    />
                    <div className="text-[11px] text-gray-500">
                        Tokens live in env vars sourced from k8s Secrets — never in this database.
                        Rotate via Secret update + deployment re-roll.
                    </div>
                </label>

                {!isDisabled && (
                    <div className={
                        "text-xs px-3 py-2 rounded-sm border " +
                        (cfg.token_present
                            ? "bg-emerald-950/40 border-emerald-700 text-emerald-200"
                            : "bg-amber-950/40 border-amber-700 text-amber-200")
                    }>
                        {cfg.token_present
                            ? `Token env var “${cfg.token_env_name}” is set on this API replica.`
                            : (cfg.token_env_name
                                ? `Token env var “${cfg.token_env_name}” is NOT set on this API replica — the bot will skip sync.`
                                : "No token env var configured yet.")
                        }
                    </div>
                )}

                <div className="flex items-center gap-3">
                    <button
                        type="button"
                        onClick={save}
                        disabled={!dirty || busy}
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm"
                    >
                        {busy ? "Saving…" : "Save"}
                    </button>
                    <button
                        type="button"
                        onClick={() => setDraft(cfg)}
                        disabled={!dirty || busy}
                        className="text-sm text-gray-400 hover:text-gray-200 disabled:opacity-50"
                    >
                        Discard
                    </button>
                    {savedAt && !dirty && (
                        <span className="text-xs text-emerald-400">
                            saved {Math.floor((Date.now() - savedAt) / 1000)}s ago
                        </span>
                    )}
                    {saveErr && (
                        <span className="text-xs text-red-400" role="alert">{saveErr}</span>
                    )}
                </div>
            </div>
        </div>
    );
};

export default IssueTargetTab;
