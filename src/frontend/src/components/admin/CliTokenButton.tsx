import React, {useEffect, useState} from "react";
import {ApiError, viewerApi} from "@/services/viewerApi";

// Header button + modal for the admin's own CLI bearer token.
// Deliberately not a tab — there's no list of tokens to manage. Mint
// hands you a fresh JWT (the previous one keeps working until it
// expires or you press Revoke); revoke bumps the per-user cutoff so
// every previously-minted token starts failing on the next use.

const CliTokenButton: React.FC = () => {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button
                type="button"
                onClick={() => setOpen(true)}
                className="text-xs px-2 py-1 rounded bg-gray-800 text-gray-200 hover:bg-gray-700 no-drag"
                title="Mint or revoke a CLI bearer token for this account"
            >
                CLI token
            </button>
            {open && <CliTokenModal onClose={() => setOpen(false)}/>}
        </>
    );
};

const CliTokenModal: React.FC<{onClose: () => void}> = ({onClose}) => {
    const [token, setToken] = useState<string | null>(null);
    const [expiresAt, setExpiresAt] = useState<number | null>(null);
    const [busy, setBusy] = useState<"mint" | "revoke" | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [revokedAt, setRevokedAt] = useState<number | null>(null);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const onMint = async () => {
        setBusy("mint");
        setErr(null);
        setRevokedAt(null);
        try {
            const r = await viewerApi.adminMintCliToken();
            setToken(r.token);
            setExpiresAt(r.expires_at);
        } catch (e) {
            setErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusy(null);
        }
    };

    const onRevoke = async () => {
        if (!confirm("Revoke every CLI token previously minted for your account?")) return;
        setBusy("revoke");
        setErr(null);
        try {
            const r = await viewerApi.adminRevokeCliTokens();
            setRevokedAt(r.revoked_at);
            setToken(null);
            setExpiresAt(null);
        } catch (e) {
            setErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusy(null);
        }
    };

    const onCopy = async () => {
        if (!token) return;
        try {
            await navigator.clipboard.writeText(token);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy */
        }
    };

    return (
        <div
            className="fixed inset-0 z-[60] flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded shadow-xl flex flex-col max-w-2xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="CLI token"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">CLI token</div>
                        <div className="text-xs text-gray-400">
                            30-day bearer for pixi tasks and other API clients.
                        </div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-auto p-4 space-y-4 text-sm">
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={onMint}
                            disabled={busy !== null}
                            className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs"
                        >
                            {busy === "mint" ? "Generating…" : "Generate new"}
                        </button>
                        <button
                            type="button"
                            onClick={onRevoke}
                            disabled={busy !== null}
                            className="bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs"
                        >
                            {busy === "revoke" ? "Revoking…" : "Revoke all"}
                        </button>
                    </div>
                    {err && (
                        <div className="text-xs text-red-300 bg-red-900/40 border border-red-700 rounded px-2 py-1">
                            {err}
                        </div>
                    )}
                    {revokedAt !== null && (
                        <div className="text-xs text-gray-300">
                            All previously-minted CLI tokens revoked at{" "}
                            {new Date(revokedAt * 1000).toLocaleString()}.
                        </div>
                    )}
                    {token && (
                        <div className="space-y-2">
                            <div className="flex items-center justify-between gap-3">
                                <div className="text-xs text-gray-400">
                                    Expires {expiresAt ? new Date(expiresAt * 1000).toLocaleString() : "?"}.
                                    Copy now — the server does not store it.
                                </div>
                                <button
                                    type="button"
                                    onClick={onCopy}
                                    className="shrink-0 bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded text-xs"
                                >
                                    {copied ? "Copied" : "Copy"}
                                </button>
                            </div>
                            <textarea
                                readOnly
                                value={token}
                                className="w-full h-32 bg-gray-950 border border-gray-700 rounded p-2 font-mono text-xs break-all"
                                onFocus={(e) => e.currentTarget.select()}
                            />
                            <pre className="text-[11px] text-gray-400 whitespace-pre-wrap">
{`# pixi
export ADAPY_API_TOKEN=<paste>
export ADAPY_API_BASE=<viewer URL>`}
                            </pre>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CliTokenButton;
