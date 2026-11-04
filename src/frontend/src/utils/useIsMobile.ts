import {useEffect, useState} from "react";

import {COMPACT_QUERY, NARROW_QUERY, WIDE_QUERY, mediaMatches} from "@/layout/breakpoints";

/** Same breakpoint as the mobile bottom sheet (utils/useBottomSheet.ts). */
export const MOBILE_QUERY = COMPACT_QUERY;

/** Whether `query` matches; follows it as the window changes. */
export function useMediaQuery(query: string): boolean {
    const [matches, setMatches] = useState(() => mediaMatches(query));
    useEffect(() => {
        if (typeof window === "undefined" || !window.matchMedia) return;
        const mq = window.matchMedia(query);
        const onChange = () => setMatches(mq.matches);
        // Re-read once mounted: the query may have changed between render and effect.
        onChange();
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, [query]);
    return matches;
}

/** True on phone-width viewports (below `compact`, 640 px). */
export function useIsMobile(): boolean {
    return useMediaQuery(COMPACT_QUERY);
}

/** True below `wide` (768 px): floating windows take the whole screen there. */
export function useIsNarrow(): boolean {
    return useMediaQuery(NARROW_QUERY);
}

/** True at or above `wide` (768 px). */
export function useIsWide(): boolean {
    return useMediaQuery(WIDE_QUERY);
}
