/** `20260825T135553Z` -> `2026-08-25 13:55Z`. Anything else is shown verbatim:
 *  a revision the grammar refuses should look wrong, not be tidied. */
export function formatRevision(rev: string | null | undefined): string {
    if (!rev) return "—";
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(rev);
    return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}Z` : rev;
}
