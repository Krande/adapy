import {create} from "zustand";
import {persist} from "zustand/middleware";

// Whether the ambient "audit sweep in progress" toast is hidden. Toggled from the Audit Runs
// panel; the toast (ConversionProgress) reads it. Persisted so the operator's choice sticks.
interface AuditToastState {
    hidden: boolean;
    setHidden: (v: boolean) => void;
    toggle: () => void;
}

export const useAuditToastStore = create<AuditToastState>()(
    persist(
        (set) => ({
            hidden: false,
            setHidden: (hidden) => set({hidden}),
            toggle: () => set((s) => ({hidden: !s.hidden})),
        }),
        {name: "ada-audit-toast"},
    ),
);
