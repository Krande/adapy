import {PANEL_CHROME} from "@/state/themeStore";
import React, {useEffect, useState} from "react";
import {IconButton, Icon} from "@/components/ui";
import {useOptionsStore} from "@/state/optionsStore";
import OptionsBody from "./options/OptionsBody";
import ShortcutsModal from "./options/ShortcutsModal";

// Classic-UI wrapper around the preferences content.
//
// The content moved to options/OptionsBody so the shell's dock can host it without the
// bordered, separately-scrolling panel this file draws — that was producing a box inside
// the dock's box. This wrapper keeps the classic UI's two layouts unchanged:
//
//   * Mobile: a full-height slide-in drawer from the left edge. There is not enough room
//     on a phone for an inline box.
//   * Desktop: an inline panel in the menu's info-box column, styled like the other info
//     boxes so the column reads as one pattern.
//
// Deleted at cutover with the rest of the classic chrome. ShortcutsModal rides here
// rather than in the body because the shell supersedes it with the command palette.

const MOBILE_QUERY = "(max-width: 767px)";

function OptionsComponent() {
    const [isMobile, setIsMobile] = useState(
        () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
    );
    const setIsOptionsVisible = useOptionsStore((s) => s.setIsOptionsVisible);

    useEffect(() => {
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);

    if (isMobile) {
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-surface-1 text-content text-sm shadow-float flex flex-col">
                <div className="flex items-center justify-between shrink-0 p-3 border-b border-edge">
                    <span className="font-bold text-base">Options</span>
                    <IconButton
                        tooltip="Close options"
                        icon={<Icon name="close" />}
                        onClick={() => setIsOptionsVisible(false)}
                    />
                </div>
                <div className="flex-1 overflow-y-auto scrollbar p-4 flex flex-col gap-4">
                    <OptionsBody />
                    <ShortcutsModal />
                </div>
            </div>
        );
    }

    return (
        <div className={`${PANEL_CHROME} min-w-80 max-w-sm text-sm space-y-3 max-h-[70vh] overflow-y-auto scrollbar`}>
            <h2 className="font-bold">Options</h2>
            <OptionsBody />
            <ShortcutsModal />
        </div>
    );
}

export default OptionsComponent;
