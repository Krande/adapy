import {useEffect, useState} from "react";

/** Same breakpoint as the mobile bottom sheet (utils/useBottomSheet.ts). */
export const MOBILE_QUERY = "(max-width: 639px)";

/** True on phone-width viewports; follows the media query as the window changes. */
export function useIsMobile(): boolean {
    const [mobile, setMobile] = useState(
        () => typeof window !== "undefined" && !!window.matchMedia?.(MOBILE_QUERY).matches,
    );
    useEffect(() => {
        if (typeof window === "undefined" || !window.matchMedia) return;
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = () => setMobile(mq.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);
    return mobile;
}
