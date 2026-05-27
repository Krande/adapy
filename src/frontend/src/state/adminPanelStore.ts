import {create} from "zustand";

export type AdminTab = "audit" | "audit_runs" | "projects" | "storage" | "workers" | "conversion";

interface AdminPanelStore {
    open: boolean;
    initialTab: AdminTab | null;
    openAdmin: (tab?: AdminTab) => void;
    closeAdmin: () => void;
    clearInitialTab: () => void;
}

// Lets non-admin-panel surfaces (e.g. the conversion-progress toast)
// pop the admin panel open at a specific tab. The toast's (i) Info
// button uses ``openAdmin("audit")`` to jump to the audit log; the
// panel reads ``initialTab`` once on mount, then clears it so a manual
// reopen lands on the user's last-used tab.
export const useAdminPanelStore = create<AdminPanelStore>((set) => ({
    open: false,
    initialTab: null,
    openAdmin: (tab) => set({open: true, initialTab: tab ?? null}),
    closeAdmin: () => set({open: false}),
    clearInitialTab: () => set({initialTab: null}),
}));
