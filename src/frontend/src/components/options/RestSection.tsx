import React, {useState} from "react";
import {runtime} from "@/runtime/config";
import {useMeStore} from "@/state/meStore";
import {useScopeStore, ScopeOption, scopeUrlPart} from "@/state/scopeStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {getUser, isAuthEnabled, signOut} from "@/services/auth/oidc";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";

// REST-mode controls inside the options drawer. Replaces the cluster
// of menu-bar buttons (scope picker / admin / user pill) — those don't
// fit on phones and clutter the top bar on desktop too. The drawer is
// mobile-friendly already, so we get readable controls for free.
//
// Admin + Convert open in-viewer modals (Rnd-hosted by
// ``InViewerPanelHost``) so the 3D model stays on screen while the
// user pokes at admin tabs or kicks off a conversion. The modal
// header carries an "open in new tab" button that pops the same UI
// in dedicated full-page ``/admin`` / ``/convert`` routes.

const RestSection: React.FC = () => {
    if (!runtime.isRestMode()) return null;
    return (
        <div className="space-y-3">
            <SignedInRow/>
            <ScopeSelector/>
            <ConvertButton/>
            <AdminButton/>
        </div>
    );
};

const ConvertButton: React.FC = () => {
    // Any authed user can hit Convert — the panel is the primary
    // upload + convert entry point and gates on scope-level access
    // server-side. Opens as an in-viewer Rnd modal so the 3D model
    // stays on screen; the modal's external-link button pops the
    // dedicated ``/convert`` page in a new tab when the user wants
    // a full-screen workspace.
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    return (
        <button
            type="button"
            onClick={() => openPanel("convert")}
            className="block w-full bg-blue-700 hover:bg-blue-600 text-white text-sm font-semibold py-1 px-2 rounded-sm"
        >
            Convert files
        </button>
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
                            className="shrink-0 bg-gray-700 hover:bg-gray-600 text-gray-100 px-1.5 py-0.5 rounded-sm text-[10px]"
                            title="Copy your OIDC sub — paste into the admin Add member form"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    </div>
                )}
            </div>
            <button
                className="bg-gray-700 hover:bg-gray-600 text-white text-xs px-2 py-1 rounded-sm"
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
        // Project switch tears down anything that belongs to the
        // outgoing scope: the file list (server-derived; the new
        // request below re-populates it), every loaded model in
        // the scene, and the selection state. Without these, the
        // user sees the old project's files lingering in the
        // panel and a stale 3D scene from a project they're no
        // longer in.
        useServerInfoStore.getState().setServerFileObjects([]);
        useServerInfoStore.getState().setServerFiles([]);
        void clear_loaded_model();
        setCurrent(picked as ScopeOption);
        // Background refresh — the response lands via the same
        // LIST_FILE_OBJECTS handler the Refresh button uses. UI
        // shows "no files yet" until it returns (~hundreds of ms).
        void request_list_of_files_from_server();
    };
    return (
        <label className="block text-xs">
            <div className="text-gray-400 mb-1">Active scope</div>
            <select
                className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 text-white"
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
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    if (!isAdmin) return null;
    return (
        <button
            type="button"
            onClick={() => openPanel("admin")}
            className="block w-full bg-purple-700 hover:bg-purple-600 text-white text-sm font-semibold py-1 px-2 rounded-sm"
        >
            Admin panel
        </button>
    );
};

export default RestSection;
