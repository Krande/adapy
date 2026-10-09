import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import type {CliTokenRecord} from "@/services/viewerApi";
import {fmtRelative, fmtTimestamp} from "../scheduleFormat";
import {
    CLI_TOKEN_TTL_DAYS,
    filterTokens,
    isCiBot,
    tokenHint,
    tokenOwner,
    tokenStatus,
    untrackedValidUntil,
} from "./cliTokens";

// The CLI tokens tab: every bearer token this deployment has issued — people's
// and CI bots' — with a per-token revoke, plus minting your own.
//
// It replaces the header "CLI token" button, which could only mint and
// "revoke all": tokens were stateless, so there was nothing to list. They are
// now recorded at mint time (never the token itself — see migration 034), and
// this is the view of that record.
//
// CI bots are minted and rotated from the Projects tab, where the bot's
// project lives; here they are listed and can be revoked like any other token.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : String(e));

const CliTokensTab: React.FC = () => {
    const [tokens, setTokens] = useState<CliTokenRecord[]>([]);
    const [trackedSince, setTrackedSince] = useState<string | null>(null);
    const [showInactive, setShowInactive] = useState(false);
    const [query, setQuery] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [label, setLabel] = useState("");
    const [fresh, setFresh] = useState<{token: string; expires_at: number; hint: string} | null>(null);

    const reload = async (inactive = showInactive) => {
        setLoading(true);
        try {
            const r = await viewerApi.adminListCliTokens(inactive);
            setTokens(r.tokens);
            setTrackedSince(r.tracked_since);
            setError(null);
        } catch (e) {
            setError(errText(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload(showInactive);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [showInactive]);

    const shown = useMemo(() => filterTokens(tokens, query), [tokens, query]);
    const active = tokens.filter((t) => tokenStatus(t) === "active");
    const activeBots = active.filter(isCiBot).length;

    const onMint = async () => {
        setBusy("mint");
        setError(null);
        try {
            const r = await viewerApi.adminMintCliToken(label);
            setFresh({token: r.token, expires_at: r.expires_at, hint: r.hint});
            setLabel("");
            await reload();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };

    const onRevoke = async (t: CliTokenRecord) => {
        const what = `${tokenHint(t)}${t.label ? ` (${t.label})` : ""} owned by ${tokenOwner(t)}`;
        if (!confirm(`Revoke token ${what}? Its next use will be refused.`)) return;
        setBusy(t.jti);
        setError(null);
        try {
            await viewerApi.adminRevokeCliToken(t.jti);
            await reload();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };

    const onRevokeAllMine = async () => {
        if (!confirm("Revoke every CLI token issued to YOUR account, including ones from before tracking?")) return;
        setBusy("revoke-all");
        setError(null);
        try {
            await viewerApi.adminRevokeCliTokens();
            setFresh(null);
            await reload();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };

    const untrackedUntil = untrackedValidUntil(trackedSince);

    return (
        <div className="flex flex-col h-full text-sm">
            <div className="flex flex-wrap items-center gap-2 px-3 sm:px-4 py-2 border-b border-gray-700 text-xs">
                <input
                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full sm:w-56 text-white"
                    placeholder="Label for a new token (optional)"
                    maxLength={120}
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && busy === null) void onMint();
                    }}
                />
                <button
                    type="button"
                    onClick={() => void onMint()}
                    disabled={busy !== null}
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-3 py-1 rounded-sm"
                >
                    {busy === "mint" ? "Generating…" : "Generate token"}
                </button>
                <button
                    type="button"
                    onClick={() => void onRevokeAllMine()}
                    disabled={busy !== null}
                    className="bg-red-800 hover:bg-red-700 disabled:opacity-50 text-white px-3 py-1 rounded-sm"
                    title="Revoke every token issued to your own account"
                >
                    {busy === "revoke-all" ? "Revoking…" : "Revoke all mine"}
                </button>
                <span className="hidden sm:inline w-px h-5 bg-gray-700 mx-1"/>
                <input
                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full sm:w-56 text-white"
                    placeholder="Search owner, label, or token tail…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
                <label className="flex items-center gap-1 text-gray-300 whitespace-nowrap">
                    <input
                        type="checkbox"
                        checked={showInactive}
                        onChange={(e) => setShowInactive(e.target.checked)}
                    />
                    revoked &amp; expired
                </label>
                <button
                    type="button"
                    className="ml-auto bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    onClick={() => void reload()}
                    disabled={loading}
                >
                    {loading ? "Loading…" : "Refresh"}
                </button>
            </div>

            <div className="px-3 sm:px-4 py-2 border-b border-gray-800 text-xs text-gray-400 space-y-1">
                <div>
                    <span className="text-gray-200 font-medium">{active.length}</span> active token
                    {active.length === 1 ? "" : "s"}
                    {active.length > 0 && ` (${active.length - activeBots} personal, ${activeBots} CI bot)`}.
                    {" "}Tokens last {CLI_TOKEN_TTL_DAYS} days.
                </div>
                {trackedSince && (
                    <div>
                        Tokens issued before {fmtTimestamp(trackedSince)} predate per-token tracking and
                        are not listed.
                        {untrackedUntil && untrackedUntil.getTime() > Date.now() && (
                            <>
                                {" "}They can stay valid until {untrackedUntil.toLocaleString()} unless
                                their owner&apos;s tokens are revoked with &quot;revoke all&quot;.
                            </>
                        )}
                    </div>
                )}
            </div>

            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">{error}</div>
            )}

            {fresh && <FreshToken fresh={fresh} onDismiss={() => setFresh(null)}/>}

            <ul className="flex-1 min-h-0 overflow-auto divide-y divide-gray-800">
                {shown.map((t) => (
                    <TokenRow key={t.jti} token={t} busy={busy === t.jti} disabled={busy !== null}
                              onRevoke={() => void onRevoke(t)}/>
                ))}
                {!loading && shown.length === 0 && (
                    <li className="px-4 py-8 text-center text-gray-500">
                        {tokens.length === 0
                            ? showInactive ? "No tokens issued yet." : "No active tokens."
                            : "No token matches the search."}
                    </li>
                )}
            </ul>
        </div>
    );
};

const STATUS_STYLE: Record<ReturnType<typeof tokenStatus>, string> = {
    active: "bg-green-900/60 text-green-200",
    expired: "bg-gray-700 text-gray-300",
    revoked: "bg-red-900/60 text-red-200",
};

const TokenRow: React.FC<{
    token: CliTokenRecord;
    busy: boolean;
    disabled: boolean;
    onRevoke: () => void;
}> = ({token: t, busy, disabled, onRevoke}) => {
    const status = tokenStatus(t);
    return (
        <li className={"px-3 sm:px-4 py-2 flex flex-wrap sm:flex-nowrap items-start gap-x-4 gap-y-1 " +
            (status === "active" ? "" : "opacity-60")}>
            <div className="w-full sm:w-44 shrink-0">
                <div className="font-mono text-xs text-gray-100" title={`token id ${t.jti}`}>{tokenHint(t)}</div>
                <div className="text-xs text-gray-400 truncate" title={t.label ?? undefined}>
                    {t.label || <span className="italic text-gray-600">no label</span>}
                </div>
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1 min-w-0">
                    <span className="truncate" title={tokenOwner(t)}>{tokenOwner(t)}</span>
                    {isCiBot(t) && (
                        <span className="rounded-sm bg-sky-900/60 text-sky-200 px-1 text-[10px] uppercase shrink-0">
                            CI bot
                        </span>
                    )}
                    {t.is_admin && (
                        <span className="rounded-sm bg-amber-900/60 text-amber-200 px-1 text-[10px] uppercase shrink-0"
                              title="Carries admin rights">
                            admin
                        </span>
                    )}
                </div>
                <div className="text-[11px] text-gray-500 font-mono truncate" title={t.sub}>{t.sub}</div>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-[11px] text-gray-400 w-full sm:w-72 shrink-0">
                <dt className="text-gray-500">issued</dt>
                <dd title={fmtTimestamp(t.issued_at)}>
                    {fmtRelative(t.issued_at)}{t.issued_by && t.issued_by !== t.sub ? ` by ${t.issued_by}` : ""}
                </dd>
                <dt className="text-gray-500">{status === "expired" ? "expired" : "expires"}</dt>
                <dd title={fmtTimestamp(t.expires_at)}>{fmtRelative(t.expires_at)}</dd>
                <dt className="text-gray-500">last used</dt>
                <dd title={fmtTimestamp(t.last_used_at)}>{t.last_used_at ? fmtRelative(t.last_used_at) : "never"}</dd>
                {t.revoked_at && (
                    <>
                        <dt className="text-gray-500">revoked</dt>
                        <dd title={fmtTimestamp(t.revoked_at)}>
                            {fmtRelative(t.revoked_at)}{t.revoked_by ? ` by ${t.revoked_by}` : ""}
                        </dd>
                    </>
                )}
            </dl>
            <div className="flex items-center gap-2 sm:w-24 shrink-0 sm:justify-end">
                <span className={"rounded-sm px-1.5 py-0.5 text-[10px] uppercase " + STATUS_STYLE[status]}>
                    {status}
                </span>
                {status === "active" && (
                    <button
                        type="button"
                        onClick={onRevoke}
                        disabled={disabled}
                        className="bg-red-800 hover:bg-red-700 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm text-xs"
                    >
                        {busy ? "…" : "Revoke"}
                    </button>
                )}
            </div>
        </li>
    );
};

const FreshToken: React.FC<{
    fresh: {token: string; expires_at: number; hint: string};
    onDismiss: () => void;
}> = ({fresh, onDismiss}) => {
    const [copied, setCopied] = useState(false);
    const onCopy = async () => {
        try {
            await navigator.clipboard.writeText(fresh.token);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — the textarea still allows select-and-copy */
        }
    };
    return (
        <div className="px-3 sm:px-4 py-3 border-b border-gray-700 bg-gray-950 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-gray-300">
                    New token <span className="font-mono">{tokenHint(fresh)}</span>, expires{" "}
                    {new Date(fresh.expires_at * 1000).toLocaleString()}. Copy it now — it is not shown again.
                </span>
                <button type="button" onClick={() => void onCopy()}
                        className="ml-auto bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded-sm">
                    {copied ? "Copied" : "Copy"}
                </button>
                <button type="button" onClick={onDismiss}
                        className="bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded-sm">
                    Done
                </button>
            </div>
            <textarea
                readOnly
                value={fresh.token}
                className="w-full h-20 bg-gray-900 border border-gray-700 rounded-sm p-2 font-mono text-xs break-all"
                onFocus={(e) => e.currentTarget.select()}
            />
            <pre className="text-[11px] text-gray-400 whitespace-pre-wrap">
{`# pixi
export ADAPY_API_TOKEN=<paste>
export ADAPY_API_BASE=<viewer URL>`}
            </pre>
        </div>
    );
};

export default CliTokensTab;
