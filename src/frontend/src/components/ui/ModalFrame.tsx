import React, {useEffect, useId, useRef} from "react";

// A modal card over the page, styled as core's confirm / prompt dialogs (`./AppDialogs`): a title,
// a body, a row of buttons. Escape or a click outside closes it; focus starts inside and returns
// where it was.

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";

export interface ModalFrameProps {
    title: string;
    onClose: () => void;
    /** The buttons under the body; a Close button when omitted. */
    footer?: React.ReactNode;
    /** Tailwind max-width class of the card. */
    width?: string;
    children: React.ReactNode;
    testId?: string;
}

export const ModalFrame: React.FC<ModalFrameProps> = ({title, onClose, footer, width = "max-w-md", children, testId}) => {
    const titleId = useId();
    const cardRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const before = document.activeElement as HTMLElement | null;
        cardRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
        return () => before?.focus?.();
    }, []);

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
            return;
        }
        if (e.key !== "Tab") return;
        const els = Array.from(cardRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
        if (els.length === 0) return;
        const first = els[0];
        const last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    };

    return (
        <div
            // Over the floating panels (z-[60]); under the dropdowns and pickers (z-[70]) and core's
            // confirm / alert (z-[80]), which the content may open.
            className="fixed inset-0 z-[65] flex items-center justify-center bg-black/40 p-4"
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) onClose();
            }}
        >
            <div
                ref={cardRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                data-testid={testId}
                onKeyDown={onKeyDown}
                className={`w-full ${width} rounded-sm border border-gray-700 bg-gray-800 text-gray-100 shadow-xl`}
            >
                <div id={titleId} className="px-4 py-3 border-b border-gray-700 text-sm font-semibold">
                    {title}
                </div>
                <div className="px-4 py-3 text-sm max-h-[70vh] overflow-y-auto">{children}</div>
                <div className="px-4 py-3 border-t border-gray-700 flex items-center justify-end gap-2">
                    {footer ?? <ModalButton onClick={onClose}>Close</ModalButton>}
                </div>
            </div>
        </div>
    );
};

export const ModalButton: React.FC<{
    onClick: () => void;
    primary?: boolean;
    disabled?: boolean;
    title?: string;
    children: React.ReactNode;
}> = ({onClick, primary, disabled, title, children}) => (
    <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        title={title}
        className={
            "px-3 py-1 rounded-sm text-xs disabled:opacity-50 " +
            (primary ? "bg-blue-700 hover:bg-blue-600 text-white" : "bg-gray-700 hover:bg-gray-600")
        }
    >
        {children}
    </button>
);
