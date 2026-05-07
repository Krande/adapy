import React, {useEffect, useState} from "react";
import {runtime} from "@/runtime/config";
import {useMeStore} from "@/state/meStore";
import {useScopeStore, ScopeOption, scopeUrlPart} from "@/state/scopeStore";
import {getUser, isAuthEnabled, signOut} from "@/services/auth/oidc";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

// REST-mode controls inside the options drawer. Replaces the cluster
// of menu-bar buttons (scope picker / admin / user pill) — those don't
// fit on phones and clutter the top bar on desktop too. The drawer is
// mobile-friendly already, so we get readable controls for free.
//
// Lazy-loaded by OptionsComponent so the desktop bundle never picks up
// admin code unless the operator explicitly opens the panel.
const AdminPanel = React.lazy(() => import("../admin/AdminPanel"));

const RestSection: React.FC = () => {
    if (!runtime.isRestMode()) return null;
    return (
        <div className="space-y-3">
            <SignedInRow/>
            <ScopeSelector/>
            <AdminButton/>
        </div>
    );
};

const SignedInRow: React.FC = () => {
    if (!isAuthEnabled()) return null;
    const user = getUser();
    const label = user.email || user.name || user.sub || "signed in";
    const sub = user.sub;
    const [copied, setCopied] = useState(false);
    const onCopy = async () => {
        if (!sub) return;
        try {
            await navigator.clipboard.writeText(sub);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — the title attr still lets users select+copy */
        }
    };
    return (
        <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 text-xs">
                <div className="text-gray-400">Signed in as</div>
                <div className="truncate" title={label}>{label}</div>
                {sub && (
                    <div className="flex items-center gap-1 mt-0.5 text-gray-400">
                        <span className="shrink-0">ID:</span>
                        <span className="truncate font-mono" title={sub}>{sub}</span>
                        <button
                            type="button"
                            onClick={() => void onCopy()}
                            className="shrink-0 bg-gray-700 hover:bg-gray-600 text-gray-100 px-1.5 py-0.5 rounded text-[10px]"
                            title="Copy your OIDC sub — paste into the admin Add member form"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    </div>
                )}
            </div>
            <button
                className="bg-gray-700 hover:bg-gray-600 text-white text-xs px-2 py-1 rounded"
                onClick={() => void signOut()}
            >
                Sign out
            </button>
        </div>
    );
};

const ScopeSelector: React.FC = () => {
    const {current, available, setCurrent} = useScopeStore();
    if (available.length <= 1) return null;
    const value = current ? scopeUrlPart(current) : "";
    const onChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const picked = available.find((s) => scopeUrlPart(s) === e.target.value);
        if (!picked) return;
        setCurrent(picked as ScopeOption);
        void request_list_of_files_from_server();
    };
    return (
        <label className="block text-xs">
            <div className="text-gray-400 mb-1">Active scope</div>
            <select
                className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1 text-white"
                value={value}
                onChange={onChange}
            >
                {available.map((s) => (
                    <option key={scopeUrlPart(s)} value={scopeUrlPart(s)}>
                        {s.name} ({s.kind})
                    </option>
                ))}
            </select>
        </label>
    );
};

const AdminButton: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [open, setOpen] = useState(false);
    // Lock background scroll while the modal is open. Belt-and-braces
    // for mobile, where the drawer + admin modal both want the
    // document scroll.
    useEffect(() => {
        if (!open) return;
        const prev = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        return () => {
            document.body.style.overflow = prev;
        };
    }, [open]);
    if (!isAdmin) return null;
    return (
        <>
            <button
                className="w-full bg-purple-700 hover:bg-purple-600 text-white text-sm font-semibold py-1 px-2 rounded"
                onClick={() => setOpen(true)}
            >
                Admin panel
            </button>
            {open && (
                <React.Suspense fallback={null}>
                    <AdminPanel onClose={() => setOpen(false)}/>
                </React.Suspense>
            )}
        </>
    );
};

export default RestSection;
