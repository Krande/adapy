import React, {useEffect, useState} from "react";
import {AdminProject, ApiError, ProjectMember, viewerApi} from "@/services/viewerApi";

// Two-pane project management: project list on the left, member detail
// on the right. Operator picks a project, then adds/removes members
// against it. Slug/name capture happens in a tiny form at the top of
// the list pane; conflicts surface as inline error text rather than
// alerts so the operator can fix and retry without losing context.

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
            // Reconcile selection — if the picked project went away
            // (archived, etc.), drop it.
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

    return (
        <div className="flex h-full">
            <div className="w-1/3 min-w-[260px] border-r border-gray-700 flex flex-col">
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
                                "w-full text-left px-3 py-2 border-b border-gray-800 hover:bg-gray-800 " +
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
            <div className="flex-1 overflow-auto">
                {selected ? (
                    <MemberPane project={selected} onArchive={() => onArchive(selected)}/>
                ) : (
                    <div className="flex h-full items-center justify-center text-gray-500 text-sm">
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
    // Auto-derive slug from name until the operator types directly into
    // the slug field — a small affordance, not a hard rule.
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
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                placeholder="Project name"
                value={name}
                onChange={(e) => setName(e.target.value)}
            />
            <input
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300"
                placeholder="slug"
                value={effectiveSlug}
                onChange={(e) => {
                    setTouchedSlug(true);
                    setSlug(e.target.value);
                }}
            />
            <button
                type="submit"
                className="w-full bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded text-sm disabled:opacity-50"
                disabled={!name.trim() || !effectiveSlug}
            >
                Create project
            </button>
        </form>
    );
};

const MemberPane: React.FC<{project: AdminProject; onArchive: () => void}> = ({
    project,
    onArchive,
}) => {
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
            <div className="px-4 py-3 border-b border-gray-700">
                <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
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
                <div className="flex gap-2 px-4 py-2 border-b border-gray-700">
                    <input
                        className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs"
                        placeholder="user_sub (from OIDC token)"
                        value={newSub}
                        onChange={(e) => setNewSub(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") void onAdd();
                        }}
                    />
                    <select
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs"
                        value={newRole}
                        onChange={(e) => setNewRole(e.target.value)}
                    >
                        <option value="member">member</option>
                        <option value="owner">owner</option>
                    </select>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded text-xs disabled:opacity-50"
                        onClick={() => void onAdd()}
                        disabled={adding || !newSub.trim()}
                    >
                        Add
                    </button>
                </div>
            )}
            {error && (
                <div className="px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 overflow-auto">
                <table className="w-full text-xs">
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
                            <Td>{m.display_name || ""}</Td>
                            <Td>{m.email || ""}</Td>
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
    <th className="px-3 py-1 font-medium text-gray-300">{children}</th>
);

const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-1 truncate max-w-[24ch]" title={title}>
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
