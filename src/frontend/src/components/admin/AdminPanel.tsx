import React, {useEffect, useState} from "react";
import {Rnd} from "react-rnd";
import {useMeStore} from "@/state/meStore";
import AuditLogTab from "./AuditLogTab";
import CliTokenButton from "./CliTokenButton";
import ProjectsTab from "./ProjectsTab";
import StorageTab from "./StorageTab";

// Modal-style admin panel. Lazy-loaded so the desktop bundle never
// pulls it in; the opener is gated on me.isAdmin.
//
// Two layouts:
// * Mobile: full-screen sheet. There isn't enough room for a floating
//   window and the user closes the tree to reach the menu anyway.
// * Desktop: a draggable + resizable Rnd window. Size persists across
//   sessions so the user keeps the layout that fit their workflow.
//   We deliberately avoid changing widths between tabs (audit /
//   projects / storage) — same min-width on every internal table —
//   so flipping tabs doesn't reflow the modal.

type Tab = "audit" | "projects" | "storage";

const STORAGE_KEY = "ada-admin-panel-rect";
const DESKTOP_QUERY = "(min-width: 768px)";

interface PanelRect {
    x: number;
    y: number;
    width: number;
    height: number;
}

function loadRect(): PanelRect | null {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const r = JSON.parse(raw);
        if (typeof r?.x === "number" && typeof r?.y === "number" &&
            typeof r?.width === "number" && typeof r?.height === "number") {
            return r;
        }
    } catch {
        /* corrupt → ignore */
    }
    return null;
}

function saveRect(r: PanelRect): void {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(r));
    } catch {
        /* quota / private mode — fine */
    }
}

function defaultRect(): PanelRect {
    if (typeof window === "undefined") {
        return {x: 0, y: 0, width: 1400, height: 900};
    }
    // Mostly-fill the viewport, leaving margin so the user can still
    // see (and click on) the canvas behind. Cap on the upper end so a
    // 4K monitor doesn't get a 3800-wide panel.
    const width = Math.min(1400, Math.round(window.innerWidth * 0.9));
    const height = Math.min(900, Math.round(window.innerHeight * 0.9));
    return {
        x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
        y: Math.max(0, Math.round((window.innerHeight - height) / 2)),
        width,
        height,
    };
}

const AdminPanel: React.FC<{onClose: () => void}> = ({onClose}) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [tab, setTab] = useState<Tab>("audit");
    const [isDesktop, setIsDesktop] = useState(
        () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
    );
    const [rect, setRect] = useState<PanelRect>(() => loadRect() || defaultRect());

    useEffect(() => {
        const mq = window.matchMedia(DESKTOP_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);

    // Esc closes the modal (matches expectations from every other
    // dialog the user has touched today).
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    if (!isAdmin) {
        return null;
    }

    const body = (
        <div className="flex flex-col h-full bg-gray-900 text-white shadow-xl rounded overflow-hidden">
            <div
                className="admin-drag-handle flex items-center justify-between border-b border-gray-700 px-3 py-2 sm:px-4 cursor-move select-none"
                title="Drag to move"
            >
                <div className="flex gap-1 text-sm">
                    <TabButton active={tab === "audit"} onClick={() => setTab("audit")}>
                        Audit
                    </TabButton>
                    <TabButton active={tab === "projects"} onClick={() => setTab("projects")}>
                        Projects
                    </TabButton>
                    <TabButton active={tab === "storage"} onClick={() => setTab("storage")}>
                        Storage
                    </TabButton>
                </div>
                <div className="flex items-center gap-2">
                    <CliTokenButton/>
                    <button
                        className="text-gray-300 hover:text-white text-2xl leading-none px-3 py-1 -my-1 no-drag"
                        onClick={onClose}
                        aria-label="close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
            </div>
            <div className="flex-1 overflow-hidden">
                {tab === "audit" && <AuditLogTab/>}
                {tab === "projects" && <ProjectsTab/>}
                {tab === "storage" && <StorageTab/>}
            </div>
        </div>
    );

    // Mobile: full-screen sheet, no drag/resize. Backdrop is the panel
    // itself — there's nothing visible behind it anyway.
    if (!isDesktop) {
        return (
            <div className="fixed inset-0 z-50 flex bg-gray-900">
                <div className="w-full h-full">{body}</div>
            </div>
        );
    }

    // Desktop: dim backdrop + draggable/resizable Rnd window.
    return (
        <>
            <div
                className="fixed inset-0 z-40 bg-black/60"
                onClick={onClose}
                aria-hidden
            />
            <Rnd
                className="z-50"
                size={{width: rect.width, height: rect.height}}
                position={{x: rect.x, y: rect.y}}
                minWidth={720}
                minHeight={400}
                bounds="window"
                dragHandleClassName="admin-drag-handle"
                cancel="button, input, select, textarea, .no-drag, table"
                onDragStop={(_e, d) => {
                    const next = {...rect, x: d.x, y: d.y};
                    setRect(next);
                    saveRect(next);
                }}
                onResizeStop={(_e, _dir, ref, _delta, position) => {
                    const next = {
                        x: position.x,
                        y: position.y,
                        width: ref.offsetWidth,
                        height: ref.offsetHeight,
                    };
                    setRect(next);
                    saveRect(next);
                }}
            >
                {body}
            </Rnd>
        </>
    );
};

const TabButton: React.FC<{
    active: boolean;
    onClick: () => void;
    children: React.ReactNode;
}> = ({active, onClick, children}) => (
    <button
        className={
            "px-3 py-2 rounded text-sm no-drag " +
            (active ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800")
        }
        onClick={onClick}
    >
        {children}
    </button>
);

export default AdminPanel;
