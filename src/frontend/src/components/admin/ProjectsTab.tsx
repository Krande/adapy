import React, {useEffect, useState} from "react";
import {AdminProject, ApiError, ProjectMember, viewerApi} from "@/services/viewerApi";

// Project management. Two layouts:
// * sm:↑ side-by-side list + member detail (the desktop two-pane view).
// * mobile — only one of {list, detail} is visible at a time, with a
//   "Back" button to return to the list. Saves horizontal real estate
//   on phones where 50/50 split is unreadable.

const ProjectsTab: React.FC = () => {
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [selected, setSelected] = useState<AdminProject | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const reload = async () => {
        setLoading(true);
        try {
            const xs = await viewerApi.adminListProjects();
            setProjects(xs);
            if (selected) {
                const still = xs.find((p) => p.id === selected.id);
                setSelected(still || null);
            }
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const onCreate = async (slug: string, name: string) => {
        setError(null);
        try {
            const p = await viewerApi.adminCreateProject(slug, name);
            await reload();
            setSelected(p);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    const onArchive = async (p: AdminProject) => {
        if (!confirm(`Archive "${p.name}"? Members will lose access.`)) return;
        try {
            await viewerApi.adminArchiveProject(p.id);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    const showDetailOnly = selected !== null; // mobile-only: pick one pane

    return (
        <div className="flex h-full">
            <div
                className={
                    "flex-col border-r border-gray-700 sm:flex sm:w-80 sm:min-w-[280px] sm:shrink-0 lg:w-96 " +
                    (showDetailOnly ? "hidden sm:flex" : "flex w-full")
                }
            >
                <CreateProjectForm onCreate={onCreate}/>
                {error && (
                    <div className="px-3 py-2 text-red-300 text-xs border-b border-gray-700">
                        {error}
                    </div>
                )}
                <div className="flex-1 min-h-0 overflow-auto">
                    {projects.map((p) => (
                        <button
                            key={p.id}
                            className={
                                "w-full text-left px-3 py-3 sm:py-2 border-b border-gray-800 hover:bg-gray-800 " +
                                (selected?.id === p.id ? "bg-gray-800" : "")
                            }
                            onClick={() => setSelected(p)}
                        >
                            <div className="flex items-center justify-between">
                                <span className="font-medium text-sm truncate" title={p.name}>
                                    {p.name}
                                </span>
                                {p.archived_at && (
                                    <span className="text-[10px] uppercase text-gray-500 ml-2">
                                        archived
                                    </span>
                                )}
                            </div>
                            <div className="text-xs text-gray-400 truncate" title={p.slug}>
                                {p.slug} · {p.member_count} member{p.member_count === 1 ? "" : "s"}
                            </div>
                        </button>
                    ))}
                    {!loading && projects.length === 0 && (
                        <div className="px-4 py-8 text-center text-gray-500 text-sm">
                            No projects yet.
                        </div>
                    )}
                </div>
            </div>
            <div
                className={
                    "flex-1 min-h-0 overflow-auto " +
                    (showDetailOnly ? "block" : "hidden sm:block")
                }
            >
                {selected ? (
                    <MemberPane
                        project={selected}
                        onArchive={() => onArchive(selected)}
                        onBack={() => setSelected(null)}
                    />
                ) : (
                    <div className="hidden sm:flex h-full items-center justify-center text-gray-500 text-sm">
                        Pick a project to manage its members.
                    </div>
                )}
            </div>
        </div>
    );
};

const CreateProjectForm: React.FC<{onCreate: (slug: string, name: string) => void}> = ({
    onCreate,
}) => {
    const [name, setName] = useState("");
    const [slug, setSlug] = useState("");
    const [touchedSlug, setTouchedSlug] = useState(false);
    const effectiveSlug = touchedSlug ? slug : autoSlug(name);
    return (
        <form
            className="px-3 py-3 border-b border-gray-700 space-y-2"
            onSubmit={(e) => {
                e.preventDefault();
                if (!name.trim() || !effectiveSlug) return;
                onCreate(effectiveSlug, name.trim());
                setName("");
                setSlug("");
                setTouchedSlug(false);
            }}
        >
            <input
                className="w-full bg-gray-800 border border-gray-700 rounded-sm px-2 py-2 text-sm"
                placeholder="Project name"
                value={name}
                onChange={(e) => setName(e.target.value)}
            />
            <input
                className="w-full bg-gray-800 border border-gray-700 rounded-sm px-2 py-2 text-xs text-gray-300"
                placeholder="slug"
                value={effectiveSlug}
                onChange={(e) => {
                    setTouchedSlug(true);
                    setSlug(e.target.value);
                }}
            />
            <button
                type="submit"
                className="w-full bg-blue-700 hover:bg-blue-600 px-2 py-2 rounded-sm text-sm disabled:opacity-50"
                disabled={!name.trim() || !effectiveSlug}
            >
                Create project
            </button>
        </form>
    );
};

const MemberPane: React.FC<{
    project: AdminProject;
    onArchive: () => void;
    onBack: () => void;
}> = ({project, onArchive, onBack}) => {
    const [members, setMembers] = useState<ProjectMember[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);
    const [newSub, setNewSub] = useState("");
    const [newRole, setNewRole] = useState("member");
    const [ciBot, setCiBot] = useState<{token: string; expires_at: number; user_sub: string} | null>(
        null,
    );
    const [ciBotBusy, setCiBotBusy] = useState(false);
    const [ciBotErr, setCiBotErr] = useState<string | null>(null);

    const reload = async () => {
        try {
            setMembers(await viewerApi.adminListMembers(project.id));
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    useEffect(() => {
        void reload();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [project.id]);

    const onAdd = async () => {
        if (!newSub.trim()) return;
        setAdding(true);
        try {
            await viewerApi.adminAddMember(project.id, newSub.trim(), newRole.trim() || "member");
            setNewSub("");
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setAdding(false);
        }
    };

    const onRemove = async (sub: string) => {
        if (!confirm(`Remove ${sub} from "${project.name}"?`)) return;
        try {
            await viewerApi.adminRemoveMember(project.id, sub);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    // A bot's name is the segment after `ci:<slug>:`. Undefined for the
    // original unnamed bot, whose subject is just `ci:<slug>` — passing no
    // name is what keeps that one addressable, so an existing token is never
    // orphaned by this becoming multi-bot.
    const ciBotName = (sub: string): string | undefined => {
        const prefix = `ci:${project.slug}:`;
        return sub.startsWith(prefix) ? sub.slice(prefix.length) : undefined;
    };

    const provisionCiBot = async (name?: string) => {
        setCiBotBusy(true);
        setCiBotErr(null);
        try {
            const r = await viewerApi.adminProvisionCiBot(project.id, name);
            setCiBot(r);
            // refresh members so the freshly-added ci:… row shows up
            await reload();
        } catch (e) {
            setCiBotErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setCiBotBusy(false);
        }
    };

    // Minting a NEW bot asks for a name. One bot per consumer is the point:
    // the revoke cutoff is stored per subject, so consumers sharing a bot
    // cannot be rotated independently and every audit row names the same
    // principal whichever of them acted. Empty is still allowed — that is the
    // original `ci:<slug>`, and it must stay reachable.
    const onMintCiBot = async () => {
        const name = prompt(
            `Name for a CI bot on "${project.name}"?\n\n` +
            "One per consumer — e.g. ada-build, e3d-worker. Each gets its own " +
            "token, its own rotation and its own audit trail.\n\n" +
            "Leave blank for the project's original unnamed bot (ci:" + project.slug + ").",
            "",
        );
        if (name === null) return;
        const trimmed = name.trim().toLowerCase();
        const sub = trimmed ? `ci:${project.slug}:${trimmed}` : `ci:${project.slug}`;
        if (
            members.some((m) => m.user_sub === sub) &&
            !confirm(
                `${sub} already exists. Rotate its token?\n\n` +
                "Every token previously issued to THIS bot stops working immediately. " +
                "Other bots on this project are unaffected.",
            )
        ) {
            return;
        }
        await provisionCiBot(trimmed || undefined);
    };

    const onRotateCiBot = async (sub: string) => {
        if (
            !confirm(
                `Rotate the token for ${sub}?\n\n` +
                "Every token previously issued to this bot stops working immediately. " +
                "Other bots on this project are unaffected.",
            )
        ) {
            return;
        }
        await provisionCiBot(ciBotName(sub));
    };

    // Revoke WITHOUT minting: for a leaked credential or a retired consumer,
    // where rotating would hand back a fresh secret nobody asked for and leave
    // the bot able to act. Membership is left alone deliberately — removing it
    // is the neighbouring button, and keeping it means this bot's audit history
    // still resolves to a named principal.
    const onRevokeCiBot = async (sub: string) => {
        if (
            !confirm(
                `Revoke every token for ${sub}?\n\n` +
                "No replacement is minted — anything using this bot stops working " +
                "until a token is rotated for it. The bot stays a project member.",
            )
        ) {
            return;
        }
        setCiBotBusy(true);
        setCiBotErr(null);
        try {
            await viewerApi.adminRevokeCiBot(project.id, ciBotName(sub));
            await reload();
        } catch (e) {
            setCiBotErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setCiBotBusy(false);
        }
    };

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 sm:px-4 py-3 border-b border-gray-700">
                <div className="flex items-center gap-2 mb-1">
                    <button
                        className="sm:hidden bg-gray-800 hover:bg-gray-700 text-xs px-2 py-1 rounded-sm"
                        onClick={onBack}
                    >
                        ← Projects
                    </button>
                    <div className="min-w-0 flex-1">
                        <div className="text-sm font-semibold truncate" title={project.name}>
                            {project.name}
                        </div>
                        <div className="text-xs text-gray-400 truncate" title={project.id}>
                            {project.slug} · {project.id}
                        </div>
                    </div>
                    {!project.archived_at && (
                        <div className="flex shrink-0 gap-1">
                            <button
                                className="text-xs bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded-sm disabled:opacity-50 whitespace-nowrap"
                                onClick={() => void onMintCiBot()}
                                disabled={ciBotBusy}
                                title="Mint a CI bot bearer for this project (one per consumer)"
                            >
                                {ciBotBusy ? "…" : "Mint CI bot"}
                            </button>
                            <button
                                className="text-xs bg-red-800 hover:bg-red-700 px-2 py-1 rounded-sm"
                                onClick={onArchive}
                            >
                                Archive
                            </button>
                        </div>
                    )}
                </div>
                {ciBotErr && (
                    <div className="mt-2 text-red-300 text-xs bg-red-900/40 border border-red-700 rounded-sm px-2 py-1">
                        {ciBotErr}
                    </div>
                )}
            </div>
            {!project.archived_at && (
                <div className="flex flex-col sm:flex-row gap-2 px-3 sm:px-4 py-2 border-b border-gray-700">
                    <input
                        className="flex-1 bg-gray-800 border border-gray-700 rounded-sm px-2 py-2 text-xs"
                        placeholder="user_sub (from OIDC token)"
                        value={newSub}
                        onChange={(e) => setNewSub(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") void onAdd();
                        }}
                    />
                    <div className="flex gap-2">
                        <select
                            className="flex-1 sm:flex-initial bg-gray-800 border border-gray-700 rounded-sm px-2 py-2 text-xs"
                            value={newRole}
                            onChange={(e) => setNewRole(e.target.value)}
                        >
                            <option value="member">member</option>
                            <option value="owner">owner</option>
                        </select>
                        <button
                            className="bg-blue-700 hover:bg-blue-600 px-3 py-2 rounded-sm text-xs disabled:opacity-50"
                            onClick={() => void onAdd()}
                            disabled={adding || !newSub.trim()}
                        >
                            Add
                        </button>
                    </div>
                </div>
            )}
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 min-h-0 overflow-auto">
                {/* Desktop / tablet table */}
                <table className="hidden sm:table w-full text-sm table-fixed min-w-[1200px]">
                    <colgroup>
                        <col className="w-56"/>
                        <col className="w-[16rem]"/>
                        <col className="w-48"/>
                        <col className="w-28"/>
                        <col className="w-48"/>
                        <col className="w-24"/>
                    </colgroup>
                    <thead className="sticky top-0 bg-gray-800 text-left">
                    <tr>
                        <Th>Display name</Th>
                        <Th>Email</Th>
                        <Th>Sub</Th>
                        <Th>Role</Th>
                        <Th>Last seen</Th>
                        <Th>{""}</Th>
                    </tr>
                    </thead>
                    <tbody>
                    {members.map((m) => (
                        <tr key={m.user_sub} className="border-t border-gray-800">
                            <Td title={m.display_name || ""}>{m.display_name || ""}</Td>
                            <Td title={m.email || ""}>{m.email || ""}</Td>
                            <Td title={m.user_sub}>{shortSub(m.user_sub)}</Td>
                            <Td>{m.role}</Td>
                            <Td title={m.last_seen_at || ""}>
                                {fmtIsoLocal(m.last_seen_at)}
                            </Td>
                            <Td>
                                {!project.archived_at && (
                                    <span className="flex gap-2 whitespace-nowrap">
                                        {m.role === "ci" && (
                                            <>
                                                <button
                                                    className="text-blue-400 hover:text-blue-300 disabled:opacity-50"
                                                    onClick={() => void onRotateCiBot(m.user_sub)}
                                                    disabled={ciBotBusy}
                                                    title="Mint a fresh token; the current one stops working"
                                                >
                                                    rotate
                                                </button>
                                                <button
                                                    className="text-amber-400 hover:text-amber-300 disabled:opacity-50"
                                                    onClick={() => void onRevokeCiBot(m.user_sub)}
                                                    disabled={ciBotBusy}
                                                    title="Kill its tokens without minting a replacement"
                                                >
                                                    revoke
                                                </button>
                                            </>
                                        )}
                                        <button
                                            className="text-red-400 hover:text-red-300"
                                            onClick={() => onRemove(m.user_sub)}
                                        >
                                            remove
                                        </button>
                                    </span>
                                )}
                            </Td>
                        </tr>
                    ))}
                    </tbody>
                </table>
                {/* Mobile cards */}
                <ul className="sm:hidden divide-y divide-gray-800">
                    {members.map((m) => (
                        <li key={m.user_sub} className="px-3 py-3 text-xs">
                            <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="text-sm font-medium truncate">
                                        {m.display_name || m.email || shortSub(m.user_sub)}
                                    </div>
                                    {m.email && m.display_name && (
                                        <div className="text-gray-400 truncate">{m.email}</div>
                                    )}
                                    <div className="text-gray-500 text-[11px] truncate" title={m.user_sub}>
                                        {shortSub(m.user_sub)} · {m.role}
                                    </div>
                                </div>
                                {!project.archived_at && (
                                    <div className="flex shrink-0 gap-1">
                                        {m.role === "ci" && (
                                            <>
                                                <button
                                                    className="text-blue-300 hover:text-blue-200 text-xs px-2 py-1 rounded-sm border border-blue-900 disabled:opacity-50"
                                                    onClick={() => void onRotateCiBot(m.user_sub)}
                                                    disabled={ciBotBusy}
                                                >
                                                    Rotate
                                                </button>
                                                <button
                                                    className="text-amber-300 hover:text-amber-200 text-xs px-2 py-1 rounded-sm border border-amber-900 disabled:opacity-50"
                                                    onClick={() => void onRevokeCiBot(m.user_sub)}
                                                    disabled={ciBotBusy}
                                                >
                                                    Revoke
                                                </button>
                                            </>
                                        )}
                                        <button
                                            className="text-red-300 hover:text-red-200 text-xs px-2 py-1 rounded-sm border border-red-900"
                                            onClick={() => onRemove(m.user_sub)}
                                        >
                                            Remove
                                        </button>
                                    </div>
                                )}
                            </div>
                        </li>
                    ))}
                </ul>
                {members.length === 0 && (
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No members yet.
                    </div>
                )}
            </div>
            {ciBot && (
                <CiBotTokenModal
                    projectSlug={project.slug}
                    userSub={ciBot.user_sub}
                    token={ciBot.token}
                    expiresAt={ciBot.expires_at}
                    onClose={() => setCiBot(null)}
                />
            )}
        </div>
    );
};

// One-shot reveal of a freshly-minted CI bot token. Mirrors
// CliTokenButton's modal chrome (dvh height clamp + clipboard copy)
// so it stays usable on phones, where the token textarea would
// otherwise push the buttons off-screen.
const CiBotTokenModal: React.FC<{
    projectSlug: string;
    userSub: string;
    token: string;
    expiresAt: number;
    onClose: () => void;
}> = ({projectSlug, userSub, token, expiresAt, onClose}) => {
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const onCopy = async () => {
        try {
            await navigator.clipboard.writeText(token);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — user can still select-and-copy */
        }
    };

    return (
        <div
            className="fixed inset-0 z-60 flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm shadow-xl flex flex-col max-w-2xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="CI bot token"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">CI bot token</div>
                        <div className="text-xs text-gray-400 truncate" title={userSub}>
                            {userSub} · expires {new Date(expiresAt * 1000).toLocaleString()}
                        </div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 min-h-0 overflow-auto p-4 space-y-4 text-sm">
                    <div className="text-xs text-gray-300">
                        Copy now — the server does not store this token. Re-mint to rotate;
                        previous tokens for this bot stop validating immediately.
                    </div>
                    <div className="flex items-center justify-end">
                        <button
                            type="button"
                            onClick={onCopy}
                            className="shrink-0 bg-gray-800 hover:bg-gray-700 text-gray-100 px-2 py-1 rounded-sm text-xs"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    </div>
                    <textarea
                        readOnly
                        value={token}
                        className="w-full h-32 bg-gray-950 border border-gray-700 rounded-sm p-2 font-mono text-xs break-all"
                        onFocus={(e) => e.currentTarget.select()}
                    />
                    <pre className="text-[11px] text-gray-400 whitespace-pre-wrap">
{`# pixi / Forgejo secret
export ADAPY_VIEWER_TOKEN=<paste>
export ADAPY_VIEWER_URL=<viewer URL>
# scope: project:${projectSlug}`}
                    </pre>
                </div>
            </div>
        </div>
    );
};

const Th: React.FC<{children: React.ReactNode}> = ({children}) => (
    <th className="px-3 py-2 font-medium text-gray-300 whitespace-nowrap">{children}</th>
);

const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-1 truncate" title={title}>
        {children}
    </td>
);

// Render an ISO-shaped UTC string in the browser's local timezone.
// "sv-SE" preserves the "YYYY-MM-DD HH:MM:SS" shape the old raw-ISO
// slice used to produce, but with the values shifted to wall clock.
function fmtIsoLocal(ts: string | null | undefined): string {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString("sv-SE");
}

function shortSub(s: string): string {
    if (!s || s.length <= 12) return s;
    return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

function autoSlug(name: string): string {
    return name
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 63);
}

export default ProjectsTab;
