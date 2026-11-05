import React, {useEffect, useId, useRef, useState} from "react";
import {createRoot, type Root} from "react-dom/client";
import type {AlertOptions, ConfirmOptions, DialogHost, DialogText, PromptOptions} from "@/plugins/dialogs";

// Core's confirm / alert / prompt (see `@/plugins/dialogs`): one modal at a time, in a root
// of its own on the page body, so a request works from anywhere, panel or not. A second
// request declines the first rather than stacking on it.

type Request =
    | {kind: "confirm"; options: ConfirmOptions; resolve: (v: boolean) => void}
    | {kind: "alert"; options: AlertOptions; resolve: (v: void) => void}
    | {kind: "prompt"; options: PromptOptions; resolve: (v: string | null) => void};

let root: Root | null = null;
let current: {id: number; request: Request} | null = null;
let seq = 0;

function decline(r: Request): void {
    if (r.kind === "confirm") r.resolve(false);
    else if (r.kind === "alert") r.resolve();
    else r.resolve(null);
}

function render(): void {
    if (!root) {
        const el = document.createElement("div");
        el.setAttribute("data-ada-dialogs", "");
        document.body.appendChild(el);
        root = createRoot(el);
    }
    const shown = current;
    root.render(
        shown ? (
            <AppDialog
                key={shown.id}
                request={shown.request}
                onDone={(accepted, text) => {
                    if (current?.id !== shown.id) return;
                    current = null;
                    const r = shown.request;
                    if (r.kind === "confirm") r.resolve(accepted);
                    else if (r.kind === "alert") r.resolve();
                    else r.resolve(accepted ? text : null);
                    render();
                }}
            />
        ) : null,
    );
}

function show(request: Request): void {
    if (typeof document === "undefined" || !document.body) {
        decline(request);
        return;
    }
    if (current) decline(current.request);
    current = {id: ++seq, request};
    render();
}

export const appDialogs: DialogHost = {
    confirm: (options) => new Promise((resolve) => show({kind: "confirm", options, resolve})),
    alert: (options) => new Promise((resolve) => show({kind: "alert", options, resolve})),
    prompt: (options) => new Promise((resolve) => show({kind: "prompt", options, resolve})),
};

function paragraphs(body: DialogText["body"]): string[] {
    if (body === undefined) return [];
    return (typeof body === "string" ? body.split("\n") : [...body]).filter((p) => p !== "");
}

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])";

/** One request's modal; exported for its markup tests. */
export const AppDialog: React.FC<{request: Request; onDone: (accepted: boolean, text: string) => void}> = ({request, onDone}) => {
    const {kind, options} = request;
    const danger = options.tone === "danger";
    const [text, setText] = useState(request.kind === "prompt" ? request.options.initial ?? "" : "");
    const titleId = useId();
    const bodyId = useId();
    const cardRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const acceptRef = useRef<HTMLButtonElement>(null);
    const cancelRef = useRef<HTMLButtonElement>(null);

    // A destructive confirmation starts on Cancel, so a stray Enter declines.
    useEffect(() => {
        const before = document.activeElement as HTMLElement | null;
        if (inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        } else if (danger && cancelRef.current) {
            cancelRef.current.focus();
        } else {
            acceptRef.current?.focus();
        }
        return () => before?.focus?.();
    }, [danger]);

    const accept = () => onDone(true, text);
    const cancel = () => onDone(kind === "alert", "");
    const body = paragraphs(options.body);
    const acceptLabel =
        request.kind === "alert" ? request.options.okLabel ?? "OK" : request.options.confirmLabel ?? "OK";
    const cancelLabel = request.kind === "alert" ? null : request.options.cancelLabel ?? "Cancel";
    const prompt = request.kind === "prompt" ? request.options : null;

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            cancel();
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
            // Above the folder picker (z-[70]), which a confirmation may be asked from.
            className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4"
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) cancel();
            }}
        >
            <div
                ref={cardRef}
                role={kind === "prompt" ? "dialog" : "alertdialog"}
                aria-modal="true"
                aria-labelledby={titleId}
                aria-describedby={body.length ? bodyId : undefined}
                onKeyDown={onKeyDown}
                className="w-full max-w-md rounded-sm border border-gray-700 bg-gray-800 text-gray-100 shadow-xl"
            >
                <div
                    id={titleId}
                    className={"px-4 py-3 border-b border-gray-700 text-sm font-semibold " + (danger ? "text-red-300" : "")}
                >
                    {options.title}
                </div>
                {(body.length > 0 || prompt) && (
                    <div className="px-4 py-3 space-y-2 text-sm">
                        {body.length > 0 && (
                            <div id={bodyId} className="space-y-1 text-gray-200 break-words max-h-64 overflow-y-auto">
                                {body.map((p, i) => <p key={i}>{p}</p>)}
                            </div>
                        )}
                        {prompt && (
                            <label className="block">
                                {prompt.label && (
                                    <span className="block text-gray-300 mb-1">{prompt.label}</span>
                                )}
                                <input
                                    ref={inputRef}
                                    type="text"
                                    value={text}
                                    placeholder={prompt.placeholder}
                                    onChange={(e) => setText(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            accept();
                                        }
                                    }}
                                    className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 text-xs"
                                />
                            </label>
                        )}
                    </div>
                )}
                <div className="px-4 py-3 border-t border-gray-700 flex items-center justify-end gap-2">
                    {cancelLabel !== null && (
                        <button
                            ref={cancelRef}
                            type="button"
                            onClick={cancel}
                            className="px-3 py-1 rounded-sm bg-gray-700 hover:bg-gray-600 text-xs"
                        >
                            {cancelLabel}
                        </button>
                    )}
                    <button
                        ref={acceptRef}
                        type="button"
                        onClick={accept}
                        className={
                            "px-3 py-1 rounded-sm text-xs text-white " +
                            (danger ? "bg-red-700 hover:bg-red-600" : "bg-blue-700 hover:bg-blue-600")
                        }
                    >
                        {acceptLabel}
                    </button>
                </div>
            </div>
        </div>
    );
};
