import {create} from "zustand";

// A confirmation you can `await` from anywhere, including non-React code.
//
// The alternative was `window.confirm`, which is blocking, unstyleable, and in the
// embed build renders the host page's origin in the prompt — so a docs page shows
// "docs.example.com says: Discard the loaded model?", which reads like a phishing
// attempt rather than part of the viewer.
//
// Only one confirmation can be pending at a time. A second request while one is open
// resolves the older one as cancelled rather than stacking dialogs: two modal
// confirmations on screen at once is never the right answer, and cancelling is the
// safe resolution for the one the user never saw.

export interface ConfirmRequest {
    title: string;
    /** Lines of body copy. Kept as data rather than JSX so this module stays
     *  renderer-free and testable under plain node. */
    body: string[];
    confirmLabel: string;
    cancelLabel?: string;
    /** `danger` when the action destroys something the user cannot get back by
     *  pressing the same button again. */
    tone?: "danger" | "default";
}

interface PendingConfirm extends ConfirmRequest {
    resolve: (ok: boolean) => void;
}

interface ConfirmState {
    pending: PendingConfirm | null;
    request: (req: ConfirmRequest) => Promise<boolean>;
    /** Answer the open confirmation. No-op when nothing is pending. */
    answer: (ok: boolean) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
    pending: null,

    request: (req) =>
        new Promise<boolean>((resolve) => {
            const previous = get().pending;
            if (previous) previous.resolve(false);
            set({pending: {...req, resolve}});
        }),

    answer: (ok) => {
        const pending = get().pending;
        if (!pending) return;
        set({pending: null});
        pending.resolve(ok);
    },
}));

/** Ask the user to confirm. Resolves true only on an explicit confirm. */
export function confirm(req: ConfirmRequest): Promise<boolean> {
    return useConfirmStore.getState().request(req);
}
