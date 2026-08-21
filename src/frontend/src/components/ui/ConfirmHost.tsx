import React from "react";
import {Button} from "./Button";
import {Dialog} from "./Dialog";
import {Field, Input} from "./Input";
import {useConfirmStore} from "@/ui/confirm";

// Renders whatever confirm() / promptText() / alertText() currently has pending. Mounted
// once per shell; callers await a promise and never render a dialog themselves.

function Body({lines}: {lines: string[]}) {
    return (
        <div className="flex flex-col gap-2">
            {lines.map((line, i) => (
                <p key={i} className={i === 0 ? "text-content" : "text-content-muted"}>
                    {line}
                </p>
            ))}
        </div>
    );
}

export default function ConfirmHost() {
    const pending = useConfirmStore((s) => s.pending);
    const answer = useConfirmStore((s) => s.answer);

    // Seeded per request. Keyed on the pending object below so reopening the dialog does
    // not inherit the last answer.
    const [text, setText] = React.useState("");
    React.useEffect(() => {
        if (pending?.kind === "prompt") setText(pending.initial ?? "");
    }, [pending]);

    if (!pending) return null;

    if (pending.kind === "prompt") {
        const value = text.trim();
        const submit = () => {
            if (!value) return;
            answer(value);
        };
        return (
            <Dialog
                open
                onClose={() => answer(null)}
                title={pending.title}
                dismissOnBackdrop={false}
                footer={
                    <>
                        <Button variant="subtle" onClick={() => answer(null)}>
                            {pending.cancelLabel ?? "Cancel"}
                        </Button>
                        <Button variant="primary" disabled={!value} onClick={submit}>
                            {pending.confirmLabel}
                        </Button>
                    </>
                }
            >
                <div className="flex flex-col gap-3">
                    {pending.body && <Body lines={pending.body} />}
                    <Field label={pending.label}>
                        <Input
                            autoFocus
                            value={text}
                            placeholder={pending.placeholder}
                            onChange={(e) => setText(e.target.value)}
                            // Enter submits, as it does in the native prompt this replaces.
                            // Without it the dialog is strictly worse than what it replaced,
                            // which is how a "nicer" component loses an argument.
                            onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                    e.preventDefault();
                                    submit();
                                }
                            }}
                        />
                    </Field>
                </div>
            </Dialog>
        );
    }

    if (pending.kind === "alert") {
        return (
            <Dialog
                open
                onClose={() => answer(true)}
                title={pending.title}
                footer={
                    <Button variant="primary" onClick={() => answer(true)}>
                        {pending.dismissLabel ?? "OK"}
                    </Button>
                }
            >
                <Body lines={pending.body} />
            </Dialog>
        );
    }

    return (
        <Dialog
            open
            onClose={() => answer(false)}
            title={pending.title}
            // A stray backdrop click must not read as "yes, throw it away". Escape and
            // Cancel both still work, and both mean no.
            dismissOnBackdrop={false}
            footer={
                <>
                    <Button variant="subtle" onClick={() => answer(false)}>
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
            <Body lines={pending.body} />
        </Dialog>
    );
}
