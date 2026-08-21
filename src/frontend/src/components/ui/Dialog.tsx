import React from "react";
import {Icon} from "../icons";
import {IconButton} from "./Button";
import {Z} from "@/shell/zIndex";
import {cn} from "./cn";

// The modal shell every dialog in the app should sit in.
//
// There were five hand-rolled versions of this (FilePickerModal, FolderPickerModal,
// ShortcutsModal, WorkerInfoModal, FieldPickerModal), each with its own backdrop
// opacity, own z-index literal, own or missing Escape handling, and own idea of
// whether clicking the backdrop closes it. This is the one answer.
//
// Deliberately NOT a <dialog> element: the embed build renders inside a host page's
// DOM, and showModal() promotes to the browser's top layer, which escapes the
// `@scope (.ada-viewer-scope)` wrapper and would land unstyled on top of the host's
// own content. A plain positioned div stays inside the scope.

export interface DialogProps {
    open: boolean;
    onClose: () => void;
    title: React.ReactNode;
    children: React.ReactNode;
    /** Rendered right-aligned in the footer. Omit for a dialog with no actions. */
    footer?: React.ReactNode;
    /** Clicking the backdrop closes. Off for dialogs where a stray click would lose
     *  work — a confirmation is exactly that case. */
    dismissOnBackdrop?: boolean;
    /** Tailwind max-width class. Defaults to a reading-width column. */
    width?: string;
}

export function Dialog({
    open,
    onClose,
    title,
    children,
    footer,
    dismissOnBackdrop = true,
    width = "max-w-md",
}: DialogProps) {
    const panelRef = React.useRef<HTMLDivElement | null>(null);

    // Escape closes, and only while open so it never shadows the viewer's own Escape
    // (which steps back through context menu → gizmo → add-mode → selection).
    React.useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.stopPropagation();
                onClose();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose]);

    // Move focus into the dialog on open. Without this the keyboard is still on
    // whatever opened it, so Enter re-triggers that control instead of the dialog's
    // default action — and for a destructive confirm that is the wrong outcome.
    React.useEffect(() => {
        if (!open) return;
        const first = panelRef.current?.querySelector<HTMLElement>(
            "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
        );
        first?.focus();
    }, [open]);

    if (!open) return null;

    return (
        <div
            className="fixed inset-0 flex items-start sm:items-center justify-center overflow-y-auto bg-black/70 p-4"
            style={{zIndex: Z.dialog}}
            onClick={dismissOnBackdrop ? onClose : undefined}
        >
            <div
                ref={panelRef}
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
                className={cn(
                    "my-auto flex w-full flex-col rounded-lg border border-edge bg-surface-1",
                    "text-content shadow-float",
                    width,
                )}
            >
                <div className="flex items-center gap-3 border-b border-edge px-4 py-3">
                    <h2 className="flex-1 text-base font-semibold">{title}</h2>
                    <IconButton tooltip="Close (Esc)" icon={<Icon name="close" />} onClick={onClose} />
                </div>

                <div className="scrollbar max-h-[70vh] flex-1 overflow-y-auto px-4 py-3 text-sm">{children}</div>

                {footer && (
                    <div className="flex items-center justify-end gap-2 border-t border-edge px-4 py-3">{footer}</div>
                )}
            </div>
        </div>
    );
}

export default Dialog;
