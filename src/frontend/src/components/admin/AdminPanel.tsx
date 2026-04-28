import React, {useEffect, useState} from "react";
import {useMeStore} from "@/state/meStore";
import AuditLogTab from "./AuditLogTab";
import ProjectsTab from "./ProjectsTab";

// Modal-style admin panel. Lazy-loaded so the desktop bundle never
// pulls it in. The opener is gated on me.isAdmin; the server enforces
// 403 on every underlying call regardless.
//
// Layout: full-screen sheet on phones (the only layout that works
// when keyboards / OS chrome eat half the viewport), centred dialog
// with margin on tablets and up.

type Tab = "audit" | "projects";

const AdminPanel: React.FC<{onClose: () => void}> = ({onClose}) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [tab, setTab] = useState<Tab>("audit");
    if (!isAdmin) {
        return null;
    }
    return (
        <div className="fixed inset-0 z-50 flex sm:items-center sm:justify-center bg-black/60">
            <div
                className="
                    flex flex-col bg-gray-900 text-white shadow-xl
                    w-full h-full
                    sm:w-[95vw] sm:h-[92vh] sm:max-w-[1600px] sm:max-h-[1000px] sm:rounded
                "
            >
                <div className="flex items-center justify-between border-b border-gray-700 px-3 py-2 sm:px-4">
                    <div className="flex gap-1 text-sm">
                        <TabButton active={tab === "audit"} onClick={() => setTab("audit")}>
                            Audit
                        </TabButton>
                        <TabButton active={tab === "projects"} onClick={() => setTab("projects")}>
                            Projects
                        </TabButton>
                    </div>
                    <button
                        className="text-gray-300 hover:text-white text-2xl leading-none px-3 py-1 -my-1"
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
            "px-3 py-2 rounded text-sm " +
            (active ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800")
        }
        onClick={onClick}
    >
        {children}
    </button>
);

export default AdminPanel;
