import React, {useCallback, useEffect, useState} from "react";
import {IssueTargetConfig, viewerApi} from "@/services/viewerApi";
import InfoIcon from "@/components/icons/InfoIcon";

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

// Per-forge setup guide, shown behind the ⓘ toggle. The bot only ever
// reads, creates and comments on issues, so the token it needs is
// narrow — spelling that out here saves a round-trip to the docs and
// discourages pasting an over-scoped credential into the secret store.
const KIND_INFO: Partial<Record<IssueTargetConfig["kind"], React.ReactNode>> = {
    github: (
        <>
            <p>
                Use a <strong>fine-grained personal access token</strong>:{" "}
                <em>Settings → Developer settings → Personal access tokens → Fine-grained tokens</em>.
            </p>
            <ul className="list-disc pl-4 space-y-1">
                <li>
                    <strong>Repository access</strong> — <em>Only select repositories</em>, and pick the
                    repository entered above.
                </li>
                <li>
                    <strong>Permissions</strong> — <code>Issues: Read and write</code>, and nothing else.
                    <code>Metadata: Read-only</code> is added automatically.
                </li>
                <li>
                    The token's account needs at least <strong>Triage</strong> on the repository. Below
                    that, GitHub silently drops the labels on a new issue — and the bot finds its own
                    issues by label, so every run would open a duplicate instead of commenting.
                </li>
                <li>
                    Organisation-owned repositories may need fine-grained tokens enabled for the
                    organisation, and the token approved by an owner, before it works.
                </li>
                <li>
                    A classic token with the <code>repo</code> scope also works, but grants full code
                    access. GitHub App installation tokens do not work: they expire hourly, and this
                    reads one static value from the environment.
                </li>
                <li>
                    <strong>Base URL</strong> — blank for github.com, <code>https://your-host/api/v3</code>{" "}
                    for Enterprise Server.
                </li>
            </ul>
        </>
    ),
    forgejo: (
        <>
            <p>
                Use a <strong>user access token</strong>:{" "}
                <em>User settings → Applications → Access tokens</em>.
            </p>
            <ul className="list-disc pl-4 space-y-1">
                <li>
                    <strong>Scope</strong> — <code>write:issue</code>, which implies read. Older versions
                    without per-resource scopes need the coarse <code>repo</code> scope instead.
                </li>
                <li>
                    The token owner must have <strong>write</strong> access to the repository, and the
                    repository must have its issue tracker enabled.
                </li>
                <li>
                    Scopes cannot be edited after creation — a token with the wrong scope has to be
                    deleted and re-issued.
                </li>
                <li>
                    <strong>Base URL is required</strong>, and must include the API root:{" "}
                    <code>https://your-host/api/v1</code>, not just the host.
                </li>
            </ul>
        </>
    ),
};

// What the bot actually does with the token — the short version of
// "why these permissions and no more". Same for either forge.
const API_CALLS_NOTE = (
    <>
        <p className="font-semibold text-gray-200">What the token is used for</p>
        <ul className="list-disc pl-4 space-y-1">
            <li>List open issues carrying the audit label, to find an existing report.</li>
            <li>Create an issue for a failure fingerprint that has no report yet.</li>
            <li>Comment on an issue when a known failure recurs.</li>
            <li>Edit the dashboard issue body so it stays a single up-to-date summary.</li>
        </ul>
    </>
);

const IssueTargetTab: React.FC = () => {
    const [cfg, setCfg] = useState<IssueTargetConfig | null>(null);
    const [loadErr, setLoadErr] = useState<string | null>(null);
    const [saveErr, setSaveErr] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [savedAt, setSavedAt] = useState<number | null>(null);
    const [showInfo, setShowInfo] = useState(false);

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
    const info = KIND_INFO[draft.kind];

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

                {info && (
                    <div className="border border-gray-700 rounded-sm">
                        <button
                            type="button"
                            onClick={() => setShowInfo((v) => !v)}
                            aria-expanded={showInfo}
                            className="flex items-center gap-2 w-full text-left px-3 py-2 text-xs text-gray-300 hover:text-white hover:bg-gray-800"
                        >
                            <InfoIcon className="w-4 h-4 shrink-0"/>
                            <span className="flex-1">
                                Which token does {draft.kind === "github" ? "GitHub" : "Forgejo / Gitea"} need,
                                and with what permissions?
                            </span>
                            <span className="text-gray-500">{showInfo ? "▾" : "▸"}</span>
                        </button>
                        {showInfo && (
                            <div
                                className="px-3 pb-3 pt-2 text-[11px] leading-relaxed text-gray-400 space-y-2 border-t border-gray-700 [&_code]:bg-gray-900 [&_code]:px-1 [&_code]:rounded-sm [&_code]:text-gray-200">
                                {info}
                                {API_CALLS_NOTE}
                            </div>
                        )}
                    </div>
                )}

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

                {!isDisabled && (
                    <label className="block text-xs text-gray-300 space-y-1">
                        <span className="block">
                            Base URL{" "}
                            <span className="text-gray-500">
                                {draft.kind === "github"
                                    ? "(optional — blank means github.com)"
                                    : "(forge API root)"}
                            </span>
                        </span>
                        <input
                            type="text"
                            value={draft.base_url}
                            onChange={(e) => setDraft({...draft, base_url: e.target.value})}
                            placeholder={draft.kind === "github"
                                ? "https://api.github.com"
                                : "https://git.example.com/api/v1"}
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
