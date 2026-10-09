// Display rules for the CLI tokens tab, kept free of React so they can be
// tested without a bundler (same split as users/userScopes.ts).

import type {CliTokenRecord} from "@/services/api/adminProjects";

export type CliTokenStatus = "active" | "expired" | "revoked";

/** Revoked wins over expired: an operator who revoked a token wants to see
 * that, even once its expiry has also passed. */
export function tokenStatus(t: CliTokenRecord, now: number = Date.now()): CliTokenStatus {
    if (t.revoked_at) return "revoked";
    return new Date(t.expires_at).getTime() <= now ? "expired" : "active";
}

/** How a token is shown in the list. Every token starts with the same JWT
 * header (``eyJ…``), so the recognisable part is the tail the server kept;
 * the leading ``eyJ`` is there so it still reads as "the end of that token". */
export function tokenHint(t: Pick<CliTokenRecord, "hint">): string {
    return `eyJ…${t.hint}`;
}

/** Owner as a person would say it: display name, else email, else the sub. */
export function tokenOwner(t: CliTokenRecord): string {
    return t.display_name || t.email || t.sub;
}

/** CI bots have a ``ci:<slug>[:<name>]`` subject; everything else is a person. */
export function isCiBot(t: Pick<CliTokenRecord, "sub">): boolean {
    return t.sub.startsWith("ci:");
}

/** Case-insensitive match on owner, sub, label, issuer and the hint, so pasting
 * the last characters of a token you found somewhere finds its row. */
export function filterTokens(tokens: CliTokenRecord[], query: string): CliTokenRecord[] {
    const q = query.trim().toLowerCase();
    if (!q) return tokens;
    return tokens.filter((t) =>
        [t.sub, t.email, t.display_name, t.label, t.issued_by, t.hint, t.jti]
            .some((v) => v != null && v.toLowerCase().includes(q))
            // A pasted full token or its tail: match on how the token ENDS.
            || q.endsWith(t.hint.toLowerCase()),
    );
}

/** The latest moment a token from before per-token tracking can still be
 * valid: tokens live 30 days, so tracking start + 30 days. */
export const CLI_TOKEN_TTL_DAYS = 30;

export function untrackedValidUntil(trackedSince: string | null): Date | null {
    if (!trackedSince) return null;
    const d = new Date(trackedSince);
    if (Number.isNaN(d.getTime())) return null;
    return new Date(d.getTime() + CLI_TOKEN_TTL_DAYS * 86_400_000);
}
