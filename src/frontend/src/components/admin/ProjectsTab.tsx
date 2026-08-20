import React, {useEffect, useState} from "react";
import {AdminProject, ApiError, ProjectMember, viewerApi} from "@/services/viewerApi";
import {confirm} from "@/ui/confirm";

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
        const ok = await confirm({
            title: "Archive this project?",
            body: [`"${p.name}" is archived and its members lose access.`],
            confirmLabel: "Archive",
            tone: "danger",
        });
        if (!ok) return;
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
                    "flex-col border-r border-edge sm:flex sm:w-80 sm:min-w-[280px] sm:shrink-0 lg:w-96 " +
                    (showDetailOnly ? "hidden sm:flex" : "flex w-full")
                }
            >
                <CreateProjectForm onCreate={onCreate}/>
                {error && (
                    <div className="px-3 py-2 text-fail text-xs border-b border-edge">
                        {error}
                    </div>
                )}
                <div className="flex-1 min-h-0 overflow-auto">
                    {projects.map((p) => (
                        <button
                            key={p.id}
                            className={
                                "w-full text-left px-3 py-3 sm:py-2 border-b border-edge hover:bg-surface-0 " +
                                (selected?.id === p.id ? "bg-surface-0" : "")
                            }
                            onClick={() => setSelected(p)}
                        >
                            <div className="flex items-center justify-between">
                                <span className="font-medium text-sm truncate" title={p.name}>
                                    {p.name}
                                </span>
                                {p.archived_at && (
                                    <span className="text-[10px] uppercase text-content-subtle ml-2">
                                        archived
                                    </span>
                                )}
                            </div>
                            <div className="text-xs text-content-muted truncate" title={p.slug}>
                                {p.slug} · {p.member_count} member{p.member_count === 1 ? "" : "s"}
                            </div>
                        </button>
                    ))}
                    {!loading && projects.length === 0 && (
                        <div className="px-4 py-8 text-center text-content-subtle text-sm">
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
                    <div className="hidden sm:flex h-full items-center justify-center text-content-subtle text-sm">
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
            className="px-3 py-3 border-b border-edge space-y-2"
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
                className="w-full bg-surface-0 border border-edge rounded-sm px-2 py-2 text-sm"
                placeholder="Project name"
                value={name}
                onChange={(e) => setName(e.target.value)}
            />
            <input
                className="w-full bg-surface-0 border border-edge rounded-sm px-2 py-2 text-xs text-content"
                placeholder="slug"
                value={effectiveSlug}
                onChange={(e) => {
                    setTouchedSlug(true);
                    setSlug(e.target.value);
                }}
            />
            <button
                type="submit"
                className="w-full bg-accent hover:bg-accent px-2 py-2 rounded-sm text-sm disabled:opacity-50"
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
        const ok = await confirm({
            title: "Remove this member?",
            body: [`${sub} loses access to "${project.name}".`],
            confirmLabel: "Remove",
            tone: "danger",
        });
        if (!ok) return;
        try {
            await viewerApi.adminRemoveMember(project.id, sub);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    const onMintCiBot = async () => {
        const existing = members.find((m) => m.role === "ci");
        const verb = existing ? "Rotate" : "Mint";
        const ok = await confirm({
            title: `${verb} the CI bot token?`,
            body: [
                `For "${project.name}".`,
                "Any token previously issued to the bot stops working.",
            ],
            confirmLabel: verb,
            tone: "danger",
        });
        if (!ok) return;
        setCiBotBusy(true);
        setCiBotErr(null);
        try {
            const r = await viewerApi.adminProvisionCiBot(project.id);
            setCiBot(r);
            // refresh members so the freshly-added ci:<slug> row shows up
            await reload();
        } catch (e) {
            setCiBotErr(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setCiBotBusy(false);
        }
    };

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 sm:px-4 py-3 border-b border-edge">
                <div className="flex items-center gap-2 mb-1">
                    <button
                        className="sm:hidden bg-surface-0 hover:bg-surface-2 text-xs px-2 py-1 rounded-sm"
                        onClick={onBack}
                    >
                        ← Projects
                    </button>
                    <div className="min-w-0 flex-1">
                        <div className="text-sm font-semibold truncate" title={project.name}>
                            {project.name}
                        </div>
                        <div className="text-xs text-content-muted truncate" title={project.id}>
                            {project.slug} · {project.id}
                        </div>
                    </div>
                    {!project.archived_at && (
                        <div className="flex shrink-0 gap-1">
                            <button
                                className="text-xs bg-surface-0 hover:bg-surface-2 px-2 py-1 rounded-sm disabled:opacity-50 whitespace-nowrap"
                                onClick={() => void onMintCiBot()}
                                disabled={ciBotBusy}
                                title="Mint or rotate the CI bot bearer for this project"
                            >
                                {ciBotBusy
                                    ? "…"
                                    : members.some((m) => m.role === "ci")
                                        ? "Rotate CI bot"
                                        : "Mint CI bot"}
                            </button>
                            <button
                                className="text-xs bg-fail hover:bg-fail px-2 py-1 rounded-sm"
                                onClick={onArchive}
                            >
                                Archive
                            </button>
                        </div>
                    )}
                </div>
                {ciBotErr && (
                    <div className="mt-2 text-fail text-xs bg-fail-subtle border border-fail rounded-sm px-2 py-1">
                        {ciBotErr}
                    </div>
                )}
            </div>
            {!project.archived_at && (
                <div className="flex flex-col sm:flex-row gap-2 px-3 sm:px-4 py-2 border-b border-edge">
                    <input
                        className="flex-1 bg-surface-0 border border-edge rounded-sm px-2 py-2 text-xs"
                        placeholder="user_sub (from OIDC token)"
                        value={newSub}
                        onChange={(e) => setNewSub(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") void onAdd();
                        }}
                    />
                    <div className="flex gap-2">
                        <select
                            className="flex-1 sm:flex-initial bg-surface-0 border border-edge rounded-sm px-2 py-2 text-xs"
                            value={newRole}
                            onChange={(e) => setNewRole(e.target.value)}
                        >
                            <option value="member">member</option>
                            <option value="owner">owner</option>
                        </select>
                        <button
                            className="bg-accent hover:bg-accent px-3 py-2 rounded-sm text-xs disabled:opacity-50"
                            onClick={() => void onAdd()}
                            disabled={adding || !newSub.trim()}
                        >
                            Add
                        </button>
                    </div>
                </div>
            )}
            {error && (
                <div className="px-3 sm:px-4 py-2 text-fail text-xs border-b border-edge">
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
                    <thead className="sticky top-0 bg-surface-0 text-left">
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
                        <tr key={m.user_sub} className="border-t border-edge">
                            <Td title={m.display_name || ""}>{m.display_name || ""}</Td>
                            <Td title={m.email || ""}>{m.email || ""}</Td>
                            <Td title={m.user_sub}>{shortSub(m.user_sub)}</Td>
                            <Td>{m.role}</Td>
                            <Td title={m.last_seen_at || ""}>
                                {fmtIsoLocal(m.last_seen_at)}
                            </Td>
                            <Td>
                                {!project.archived_at && (
                                    <button
                                        className="text-fail hover:text-fail"
                                        onClick={() => onRemove(m.user_sub)}
                                    >
                                        remove
                                    </button>
                                )}
                            </Td>
                        </tr>
                    ))}
                    </tbody>
                </table>
                {/* Mobile cards */}
                <ul className="sm:hidden divide-y divide-edge">
                    {members.map((m) => (
                        <li key={m.user_sub} className="px-3 py-3 text-xs">
                            <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="text-sm font-medium truncate">
                                        {m.display_name || m.email || shortSub(m.user_sub)}
                                    </div>
                                    {m.email && m.display_name && (
                                        <div className="text-content-muted truncate">{m.email}</div>
                                    )}
                                    <div className="text-content-subtle text-[11px] truncate" title={m.user_sub}>
                                        {shortSub(m.user_sub)} · {m.role}
                                    </div>
                                </div>
                                {!project.archived_at && (
                                    <button
                                        className="text-fail hover:text-fail text-xs px-2 py-1 rounded-sm border border-fail"
                                        onClick={() => onRemove(m.user_sub)}
                                    >
                                        Remove
                                    </button>
                                )}
                            </div>
                        </li>
                    ))}
                </ul>
                {members.length === 0 && (
                    <div className="px-4 py-8 text-center text-content-subtle text-sm">
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
                className="bg-surface-0 border border-edge rounded-sm shadow-xl flex flex-col max-w-2xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="CI bot token"
            >
                <div className="flex items-start gap-3 border-b border-edge px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">CI bot token</div>
                        <div className="text-xs text-content-muted truncate" title={userSub}>
                            {userSub} · expires {new Date(expiresAt * 1000).toLocaleString()}
                        </div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-content hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 min-h-0 overflow-auto p-4 space-y-4 text-sm">
                    <div className="text-xs text-content">
                        Copy now — the server does not store this token. Re-mint to rotate;
                        previous tokens for this bot stop validating immediately.
                    </div>
                    <div className="flex items-center justify-end">
                        <button
                            type="button"
                            onClick={onCopy}
                            className="shrink-0 bg-surface-0 hover:bg-surface-2 text-content px-2 py-1 rounded-sm text-xs"
                        >
                            {copied ? "Copied" : "Copy"}
                        </button>
                    </div>
                    <textarea
                        readOnly
                        value={token}
                        className="w-full h-32 bg-surface-0 border border-edge rounded-sm p-2 font-mono text-xs break-all"
                        onFocus={(e) => e.currentTarget.select()}
                    />
                    <pre className="text-[11px] text-content-muted whitespace-pre-wrap">
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
    <th className="px-3 py-2 font-medium text-content whitespace-nowrap">{children}</th>
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
