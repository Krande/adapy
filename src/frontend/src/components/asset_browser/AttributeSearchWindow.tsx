// The attribute search's own floating window: over the viewer, dragged by its title bar, resized
// from any edge or corner, and independent of the in-viewer panel host -- Admin or Convert can be
// open beside it.
//
// Its place and size are remembered per browser and always pass through `clampRect` (on open, and
// whenever the browser window is resized), so a position saved on a larger screen still opens on
// this one. On a phone it takes the screen, as the panel host's windows do. All of that is
// `FloatingWindow`'s.
//
// Drawn in a portal over the page so its coordinates are the viewport's, whatever the layout the
// launcher sits in. The search itself is `AttributeSearchPanel`; its state is the shared store, so
// closing the window loses nothing.

import React, {lazy} from "react";

import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {CloseGlyph, WindowFrame, WindowHeaderButton} from "@/components/ui/PanelFrame";
import {SEARCH_WINDOW_TITLE, useSearchWindowStore} from "@/state/searchWindowStore";

const AttributeSearchPanel = lazy(() => import("./AttributeSearchPanel"));

const DRAG_HANDLE = "search-window-drag-handle";

/** The search's window, around whatever body it is given. */
export const SearchWindow: React.FC<{onClose: () => void; children: React.ReactNode}> = ({onClose, children}) => (
    <FloatingWindow
        dragHandleClassName={DRAG_HANDLE}
        zIndex={61}
        zClassName="z-[61]"
        minSize={{width: 420, height: 320}}
        remember={{key: "ada.assets.attributeSearch.window", preferred: {width: 1000, height: 640}}}
        overlay
        fullScreenWhenNarrow
    >
        {({fullScreen}) => (
            <WindowFrame
                title={SEARCH_WINDOW_TITLE}
                subtitle="· every provider in this scope"
                dragHandleClassName={fullScreen ? null : DRAG_HANDLE}
                actions={
                    <WindowHeaderButton
                        onClick={onClose}
                        title="Close (the results are kept)"
                        ariaLabel="Close attribute search"
                        className="ml-auto"
                    >
                        <CloseGlyph/>
                    </WindowHeaderButton>
                }
            >
                {children}
            </WindowFrame>
        )}
    </FloatingWindow>
);

const AttributeSearchWindow: React.FC = () => {
    const open = useSearchWindowStore((s) => s.open);
    const hide = useSearchWindowStore((s) => s.hide);
    if (!open) return null;
    return (
        <SearchWindow onClose={hide}>
            <AttributeSearchPanel/>
        </SearchWindow>
    );
};

export default AttributeSearchWindow;
