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
                    "flex-col border-r border-gray-700 sm:flex sm:w-80 sm:min-w-[280px] sm:flex-shrink-0 lg:w-96 " +
                    (showDetailOnly ? "hidden sm:flex" : "flex w-full")
                }
            >
                <CreateProjectForm onCreate={onCreate}/>
                {error && (
                    <div className="px-3 py-2 text-red-300 text-xs border-b border-gray-700">
                        {error}
                    </div>
                )}
                <div className="flex-1 overflow-auto">
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
                    "flex-1 overflow-auto " +
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
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-2 text-sm"
                placeholder="Project name"
                value={name}
                onChange={(e) => setName(e.target.value)}
            />
            <input
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-2 text-xs text-gray-300"
                placeholder="slug"
                value={effectiveSlug}
                onChange={(e) => {
                    setTouchedSlug(true);
                    setSlug(e.target.value);
                }}
            />
            <button
                type="submit"
                className="w-full bg-blue-700 hover:bg-blue-600 px-2 py-2 rounded text-sm disabled:opacity-50"
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

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 sm:px-4 py-3 border-b border-gray-700">
                <div className="flex items-center gap-2 mb-1">
                    <button
                        className="sm:hidden bg-gray-800 hover:bg-gray-700 text-xs px-2 py-1 rounded"
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
                        <button
                            className="text-xs bg-red-800 hover:bg-red-700 px-2 py-1 rounded"
                            onClick={onArchive}
                        >
                            Archive
                        </button>
                    )}
                </div>
            </div>
            {!project.archived_at && (
                <div className="flex flex-col sm:flex-row gap-2 px-3 sm:px-4 py-2 border-b border-gray-700">
                    <input
                        className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-2 text-xs"
                        placeholder="user_sub (from OIDC token)"
                        value={newSub}
                        onChange={(e) => setNewSub(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") void onAdd();
                        }}
                    />
                    <div className="flex gap-2">
                        <select
                            className="flex-1 sm:flex-initial bg-gray-800 border border-gray-700 rounded px-2 py-2 text-xs"
                            value={newRole}
                            onChange={(e) => setNewRole(e.target.value)}
                        >
                            <option value="member">member</option>
                            <option value="owner">owner</option>
                        </select>
                        <button
                            className="bg-blue-700 hover:bg-blue-600 px-3 py-2 rounded text-xs disabled:opacity-50"
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
            <div className="flex-1 overflow-auto">
                {/* Desktop / tablet table */}
                <table className="hidden sm:table w-full text-sm table-fixed">
                    <colgroup>
                        <col/>
                        <col/>
                        <col className="w-[12rem]"/>
                        <col className="w-[7rem]"/>
                        <col className="w-[12rem]"/>
                        <col className="w-[6rem]"/>
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
                                {m.last_seen_at ? m.last_seen_at.replace("T", " ").slice(0, 19) : "—"}
                            </Td>
                            <Td>
                                {!project.archived_at && (
                                    <button
                                        className="text-red-400 hover:text-red-300"
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
                                    <button
                                        className="text-red-300 hover:text-red-200 text-xs px-2 py-1 rounded border border-red-900"
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
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No members yet.
                    </div>
                )}
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
