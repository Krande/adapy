import {create} from "zustand";

// Modal questions you can `await` from anywhere, including non-React code:
// `confirm()`, `promptText()`, `alertText()`.
//
// The alternative was the browser's own `confirm` / `prompt` / `alert`, which this
// replaces. They are blocking, unstyleable, and visibly not part of the application —
// a native prompt appearing over a dark themed viewer reads as a different program.
// In the embed build they are worse still: the dialog is prefixed with the HOST page's
// origin, so a docs page shows "docs.example.com says: Name for the new procedural
// model:", which looks like a phishing attempt rather than part of the viewer.
//
// Only one request can be pending at a time. A second one while the first is open
// resolves the older as cancelled rather than stacking dialogs: two modals at once is
// never the right answer, and cancelling is the safe resolution for the one the user
// never saw.

export interface ConfirmRequest {
    title: string;
    /** Lines of body copy. Data rather than JSX so this module stays renderer-free and
     *  testable under plain node. */
    body: string[];
    confirmLabel: string;
    cancelLabel?: string;
    /** `danger` when the action destroys something the user cannot get back. */
    tone?: "danger" | "default";
}

export interface PromptRequest {
    title: string;
    body?: string[];
    /** Field label — say what the value is FOR, not just "Name". */
    label: string;
    placeholder?: string;
    initial?: string;
    confirmLabel: string;
    cancelLabel?: string;
}

export interface AlertRequest {
    title: string;
    body: string[];
    tone?: "danger" | "default";
    dismissLabel?: string;
}

type Pending =
    | ({kind: "confirm"} & ConfirmRequest & {resolve: (ok: boolean) => void})
    | ({kind: "prompt"} & PromptRequest & {resolve: (value: string | null) => void})
    | ({kind: "alert"} & AlertRequest & {resolve: (ok: boolean) => void});

interface DialogState {
    pending: Pending | null;
    /** Answer the open dialog. `value` carries a prompt's text; null means cancelled. */
    answer: (value: boolean | string | null) => void;
    open: (p: Omit<Pending, "resolve">, resolve: (v: never) => void) => void;
}

export const useConfirmStore = create<DialogState>((set, get) => ({
    pending: null,

    open: (p, resolve) => {
        const previous = get().pending;
        // Resolve the unseen one as cancelled — false for confirm/alert, null for prompt.
        if (previous) (previous.resolve as (v: unknown) => void)(previous.kind === "prompt" ? null : false);
        set({pending: {...p, resolve} as Pending});
    },

    answer: (value) => {
        const pending = get().pending;
        if (!pending) return;
        set({pending: null});
        (pending.resolve as (v: unknown) => void)(value);
    },
}));

/** Ask the user to confirm. Resolves true only on an explicit confirm. */
export function confirm(req: ConfirmRequest): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
        useConfirmStore.getState().open({kind: "confirm", ...req}, resolve as never);
    });
}

/** Ask for a line of text. Resolves null when cancelled or left blank. */
export function promptText(req: PromptRequest): Promise<string | null> {
    return new Promise<string | null>((resolve) => {
        useConfirmStore.getState().open({kind: "prompt", ...req}, resolve as never);
    });
}

/** Tell the user something they must acknowledge. Resolves when dismissed. */
export function alertText(req: AlertRequest): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
        useConfirmStore.getState().open({kind: "alert", ...req}, resolve as never);
    });
}
