import {PROCEDURAL_PARAM} from "@/hooks/proceduralDeepLink";

// The query params useUrlParamLoad consumes, and so the only ones it strips. The rest
// belong to someone else and may not have been read yet: ``?mode=`` is the shell's,
// read when the shell installs - which can be AFTER the scope list arrives in
// useUrlParamLoad, so stripping the whole query string made a
// ``?file=...&mode=capacity`` link open in whichever mode won that race.
// A module of its own so it is testable without the hook's imports.

/** What useUrlParamLoad reads from the query string, and so what it strips. */
export const DEEP_LINK_PARAMS = ["file", "scope", "derived", PROCEDURAL_PARAM] as const;

/** ``search`` without the deep-link params, for ``history.replaceState`` ("" when none remain). */
export function withoutDeepLinkParams(search: string): string {
    const params = new URLSearchParams(search);
    for (const key of DEEP_LINK_PARAMS) params.delete(key);
    const rest = params.toString();
    return rest ? `?${rest}` : "";
}
