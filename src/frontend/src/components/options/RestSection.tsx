import React, {useState} from "react";
import {Button, Icon, Select} from "@/components/ui";
import {runtime} from "@/runtime/config";
import {useMeStore} from "@/state/meStore";
import {useScopeStore, ScopeOption, scopeUrlPart} from "@/state/scopeStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {getUser, isAuthEnabled, signOut} from "@/services/auth/oidc";
import {applyScopeChange} from "@/utils/scope/applyScopeChange";

// REST-mode controls inside the options drawer: identity, scope, and the entry points to
// Convert and Admin.
//
// Re-chromed. The old version had a blue "Convert files" and a PURPLE "Admin panel"
// stacked full-width — two maximally-loud buttons for things you press occasionally, and
// a colour (purple) that meant nothing anywhere else in the product. They are now ordinary
// secondary actions; admin is marked by a badge rather than by being shouted.
//
// In the shell, Convert and Admin are Data-mode panels, so these buttons are the classic
// UI's route to them and disappear at cutover along with the drawer.

const RestSection: React.FC = () => {
    if (!runtime.isRestMode()) return null;
    return (
        <div className="flex flex-col gap-3">
            <SignedInRow />
            <ScopeSelector />
            <div className="flex flex-col gap-2">
                <ConvertButton />
                <AdminButton />
            </div>
        </div>
    );
};

const ConvertButton: React.FC = () => {
    // Any authed user can hit Convert — the panel is the primary upload + convert entry
    // point and gates on scope-level access server-side. Opens as an in-viewer modal so
    // the 3D model stays on screen.
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    return (
        <Button variant="secondary" block iconLeft={<Icon name="reload" size="sm" />} onClick={() => openPanel("convert")}>
            Convert files
        </Button>
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
        <div className="flex items-start gap-2">
            <div className="flex-1 min-w-0 text-xs">
                <div className="text-content-muted">Signed in as</div>
                <div className="truncate text-content" title={label}>
                    {label}
                </div>
                {sub && (
                    // The ID itself is the copy control — click it to copy the OIDC sub
                    // (no separate button).
                    <div className="flex items-center gap-1 mt-0.5">
                        <span className="shrink-0 text-content-muted">ID:</span>
                        <button
                            type="button"
                            onClick={() => void onCopy()}
                            className={
                                "ada-focus truncate font-mono min-w-0 text-left text-content cursor-pointer " +
                                "underline decoration-dotted underline-offset-2 pointer-fine:hover:text-accent"
                            }
                            title="Click to copy your OIDC sub — paste into the admin Add member form"
                        >
                            {copied ? "Copied ✓" : sub}
                        </button>
                    </div>
                )}
            </div>
            <Button size="sm" variant="subtle" onClick={() => void signOut()}>
                Sign out
            </Button>
        </div>
    );
};

const ScopeSelector: React.FC = () => {
    const {current, available} = useScopeStore();
    if (available.length <= 1) return null;
    const value = current ? scopeUrlPart(current) : "";
    const onChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const picked = available.find((s) => scopeUrlPart(s) === e.target.value);
        if (!picked) return;
        // The teardown (clear the file list, unload the scene, refresh) lives in
        // applyScopeChange so this drawer and the shell's title-bar picker cannot drift.
        applyScopeChange(picked as ScopeOption);
    };
    return (
        <label className="flex flex-col gap-1">
            <span className="text-xs text-content-muted">Active scope</span>
            <Select fieldSize="sm" value={value} onChange={onChange}>
                {available.map((s) => (
                    <option key={scopeUrlPart(s)} value={scopeUrlPart(s)}>
                        {s.name} ({s.kind})
                    </option>
                ))}
            </Select>
        </label>
    );
};

const AdminButton: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const openPanel = useViewerPanelStore((s) => s.openPanel);
    if (!isAdmin) return null;
    return (
        <Button variant="secondary" block iconLeft={<Icon name="settings" size="sm" />} onClick={() => openPanel("admin")}>
            Admin panel
        </Button>
    );
};

export default RestSection;
