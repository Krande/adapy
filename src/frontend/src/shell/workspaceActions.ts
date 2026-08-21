import {confirm, promptText} from "@/ui/confirm";
import {useLayoutStore} from "./layoutStore";

// Saving and forgetting named workspaces.
//
// Separate from commands.ts because these ask the user something, and commands.ts is
// built on every render — an async prompt belongs behind a function call, not inline in a
// list that is rebuilt constantly.

/** Ask for a name, then capture every mode's current arrangement under it. */
export async function saveWorkspacePrompt(): Promise<void> {
    const existing = Object.keys(useLayoutStore.getState().workspaces);
    const name = await promptText({
        title: "Save workspace",
        body: [
            "Captures how every mode is arranged right now — docks, tabs, floats and sizes.",
            ...(existing.length ? [`Existing: ${existing.join(", ")}`] : []),
        ],
        label: "Workspace name",
        placeholder: "e.g. Review, Two screens, Modelling",
        confirmLabel: "Save",
    });
    if (!name) return;

    // Overwriting silently is how you lose an arrangement you spent time on. Ask, and say
    // which one — the name alone reads as a typo warning otherwise.
    if (existing.includes(name)) {
        const ok = await confirm({
            title: `Replace the "${name}" workspace?`,
            body: ["Its saved arrangement is overwritten with the current one."],
            confirmLabel: "Replace",
            tone: "danger",
        });
        if (!ok) return;
    }
    useLayoutStore.getState().saveWorkspace(name);
}

/** Pick one to forget. */
export async function forgetWorkspacePrompt(): Promise<void> {
    const names = Object.keys(useLayoutStore.getState().workspaces);
    if (names.length === 0) return;

    const name = await promptText({
        title: "Forget a workspace",
        body: [`Saved: ${names.join(", ")}`, "Layouts currently on screen are not affected."],
        label: "Which workspace",
        placeholder: names[0],
        confirmLabel: "Forget",
    });
    if (!name) return;
    if (!names.includes(name)) return;
    useLayoutStore.getState().deleteWorkspace(name);
}
