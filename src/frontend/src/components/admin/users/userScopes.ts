// Pure helpers behind the Users tab. React-free for the same reason as
// adminTabs.ts: the rules are the part worth testing, and the component pulls
// the whole viewer in.

import type {AdminUser} from "@/services/api/adminUsers";

export interface UserScope {
    kind: "user" | "shared" | "project" | "corpus";
    /** Scope id as the audit log records it (``scope_id``); null for shared. */
    id: string | null;
    name: string;
    /** Project role; absent for the implicit scopes. */
    role?: string;
    archived?: boolean;
    /** Implicit scopes come with the account, not from a membership row. */
    implicit: boolean;
}

/** The name to show for a user. The sub is the last resort, not an error: a
 * CI bot or a sub added to a project before its first sign-in has nothing
 * else. */
export function userLabel(u: Pick<AdminUser, "sub" | "display_name" | "email">): string {
    return u.display_name || u.email || u.sub;
}

/** Every scope the user can reach, in the order ``/api/me`` offers them.
 *
 * Mirrors that endpoint on purpose — Personal and Shared come with every
 * account, projects come from membership rows, corpora from the admin flag —
 * so this panel and the user's own scope picker never disagree. Archived
 * memberships are kept (flagged) rather than dropped: the audit trail still
 * references them, and "was in X" is exactly what someone reading it asks. */
export function userScopes(u: AdminUser): UserScope[] {
    const out: UserScope[] = [
        {kind: "user", id: u.sub, name: "Personal", implicit: true},
        {kind: "shared", id: null, name: "Shared", implicit: true},
    ];
    for (const p of u.projects) {
        out.push({
            kind: "project",
            id: p.id,
            name: p.name,
            role: p.role,
            archived: p.archived,
            implicit: false,
        });
    }
    if (u.is_admin) {
        out.push({kind: "corpus", id: null, name: "All corpora (admin)", implicit: true});
    }
    return out;
}

/** Case-insensitive match on name, email and sub — the three things an
 * operator might have in hand when looking someone up. */
export function filterUsers(users: readonly AdminUser[], query: string): AdminUser[] {
    const q = query.trim().toLowerCase();
    if (!q) return [...users];
    return users.filter((u) =>
        [u.display_name, u.email, u.sub].some((v) => !!v && v.toLowerCase().includes(q)),
    );
}
