import React from "react";
import {Button} from "./Button";
import {Dialog} from "./Dialog";
import {useConfirmStore} from "@/ui/confirm";

// Renders whatever `confirm()` currently has pending. Mounted once per shell; the
// callers never render a dialog themselves, they just await the promise.

export default function ConfirmHost() {
    const pending = useConfirmStore((s) => s.pending);
    const answer = useConfirmStore((s) => s.answer);

    const cancel = React.useCallback(() => answer(false), [answer]);

    if (!pending) return null;

    return (
        <Dialog
            open
            onClose={cancel}
            title={pending.title}
            // A stray backdrop click must not read as "yes, throw it away". Escape and
            // Cancel both still work, and both mean no.
            dismissOnBackdrop={false}
            footer={
                <>
                    <Button variant="subtle" onClick={cancel}>
                        {pending.cancelLabel ?? "Cancel"}
                    </Button>
                    <Button
                        variant={pending.tone === "danger" ? "danger" : "primary"}
                        onClick={() => answer(true)}
                    >
                        {pending.confirmLabel}
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-2">
                {pending.body.map((line, i) => (
                    <p key={i} className={i === 0 ? "text-content" : "text-content-muted"}>
                        {line}
                    </p>
                ))}
            </div>
        </Dialog>
    );
}
