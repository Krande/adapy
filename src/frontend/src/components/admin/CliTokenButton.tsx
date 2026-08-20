import React, {useEffect, useState} from "react";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {confirm} from "@/ui/confirm";

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
                className="text-xs px-2 py-1 rounded-sm bg-surface-0 text-content hover:bg-surface-2 no-drag"
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
        const ok = await confirm({
            title: "Revoke every CLI token?",
            body: ["Every token previously minted for your account stops working."],
            confirmLabel: "Revoke all",
            tone: "danger",
        });
        if (!ok) return;
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
            className="fixed inset-0 z-60 flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-surface-0 border border-edge rounded-sm shadow-xl flex flex-col max-w-2xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="CLI token"
            >
                <div className="flex items-start gap-3 border-b border-edge px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">CLI token</div>
                        <div className="text-xs text-content-muted">
                            30-day bearer for pixi tasks and other API clients.
                        </div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-content hover:text-white text-xl leading-none px-2"
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
                            className="bg-accent hover:bg-accent disabled:opacity-50 text-white px-3 py-1.5 rounded-sm text-xs"
                        >
                            {busy === "mint" ? "Generating…" : "Generate new"}
                        </button>
                        <button
                            type="button"
                            onClick={onRevoke}
                            disabled={busy !== null}
                            className="bg-fail hover:bg-fail disabled:opacity-50 text-white px-3 py-1.5 rounded-sm text-xs"
                        >
                            {busy === "revoke" ? "Revoking…" : "Revoke all"}
                        </button>
                    </div>
                    {err && (
                        <div className="text-xs text-fail bg-fail-subtle border border-fail rounded-sm px-2 py-1">
                            {err}
                        </div>
                    )}
                    {revokedAt !== null && (
                        <div className="text-xs text-content">
                            All previously-minted CLI tokens revoked at{" "}
                            {new Date(revokedAt * 1000).toLocaleString()}.
                        </div>
                    )}
                    {token && (
                        <div className="space-y-2">
                            <div className="flex items-center justify-between gap-3">
                                <div className="text-xs text-content-muted">
                                    Expires {expiresAt ? new Date(expiresAt * 1000).toLocaleString() : "?"}.
                                    Copy now — the server does not store it.
                                </div>
                                <button
                                    type="button"
                                    onClick={onCopy}
                                    className="shrink-0 bg-surface-0 hover:bg-surface-2 text-content px-2 py-1 rounded-sm text-xs"
                                >
                                    {copied ? "Copied" : "Copy"}
                                </button>
                            </div>
                            <textarea
                                readOnly
                                value={token}
                                className="w-full h-32 bg-surface-0 border border-edge rounded-sm p-2 font-mono text-xs break-all"
                                onFocus={(e) => e.currentTarget.select()}
                            />
                            <pre className="text-[11px] text-content-muted whitespace-pre-wrap">
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
