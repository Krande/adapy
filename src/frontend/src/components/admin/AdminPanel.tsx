import React, {useState} from "react";
import {useMeStore} from "@/state/meStore";
import AuditLogTab from "./AuditLogTab";
import ProjectsTab from "./ProjectsTab";

// Modal-style admin panel shown when the menu-bar admin button is
// clicked. Lazy-loaded so the desktop bundle never pulls it in. The
// admin button itself is gated on me.isAdmin, so an unprivileged user
// can't even ask for this chunk — but the server enforces 403 on every
// underlying call regardless.

type Tab = "audit" | "projects";

const AdminPanel: React.FC<{onClose: () => void}> = ({onClose}) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [tab, setTab] = useState<Tab>("audit");
    if (!isAdmin) {
        // Defensive: shouldn't render at all without isAdmin, but if
        // someone forces it open we don't want stray network calls.
        return null;
    }
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
            <div className="w-[min(1100px,95vw)] h-[min(720px,90vh)] flex flex-col rounded bg-gray-900 text-white shadow-xl">
                <div className="flex items-center justify-between border-b border-gray-700 px-4 py-2">
                    <div className="flex gap-1 text-sm">
                        <TabButton active={tab === "audit"} onClick={() => setTab("audit")}>
                            Audit log
                        </TabButton>
                        <TabButton active={tab === "projects"} onClick={() => setTab("projects")}>
                            Projects
                        </TabButton>
                    </div>
                    <button
                        className="text-gray-400 hover:text-white text-lg leading-none px-2"
                        onClick={onClose}
                        aria-label="close"
                        title="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-hidden">
                    {tab === "audit" ? <AuditLogTab/> : <ProjectsTab/>}
                </div>
            </div>
        </div>
    );
};

const TabButton: React.FC<{
    active: boolean;
    onClick: () => void;
    children: React.ReactNode;
}> = ({active, onClick, children}) => (
    <button
        className={
            "px-3 py-1 rounded " +
            (active ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800")
        }
        onClick={onClick}
    >
        {children}
    </button>
);

export default AdminPanel;
