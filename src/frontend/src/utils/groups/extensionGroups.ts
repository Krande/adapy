// The groups a GLB carries in its root `ADA_EXT_data` extension, as the Scene panel's "FEM groups"
// picker lists them.
//
// The three.js loader keeps the extension as an object on the model's scene group, and the panel
// reads it there. A viewport that builds no three.js scene announces the extension as JSON text
// with the model it loaded: the groups are read from it here, kept per source, and put in the
// store the picker lists. No three.js in this module.

import {useSceneInfoStore, type GroupInfo} from "@/state/sceneInfoStore";
import {activeViewport, onModelLoaded, type LoadedModelInfo} from "@/viewport/api/viewportApi";

interface ExtensionGroup {
    name?: string;
    description?: string;
    members?: string[];
    fe_object_type?: "node" | "element";
}

interface ExtensionObject {
    name?: string;
    groups?: ExtensionGroup[];
}

/** The part of the extension the groups are read from. */
export interface GroupsExtension {
    design_objects?: ExtensionObject[];
    simulation_objects?: ExtensionObject[];
}

/** Every group of one model's extension, tagged with the source it came from. A group's member
 *  list is the extension's own array, not a copy. */
export function groupsFromExtension(ext: GroupsExtension | null | undefined, source: string | undefined): GroupInfo[] {
    const groups: GroupInfo[] = [];
    for (const designObj of ext?.design_objects ?? []) {
        for (const group of designObj.groups ?? []) {
            groups.push({
                name: group.name || "Unnamed Group",
                description: group.description,
                members: group.members,
                type: "design" as const,
                parent_name: designObj.name || "Unnamed Object",
                source,
            });
        }
    }
    for (const simObj of ext?.simulation_objects ?? []) {
        for (const group of simObj.groups ?? []) {
            groups.push({
                name: group.name || "Unnamed Group",
                description: group.description,
                members: group.members,
                type: "simulation" as const,
                parent_name: simObj.name || "Unnamed Object",
                fe_object_type: group.fe_object_type,
                source,
            });
        }
    }
    return groups;
}

/** The groups of an extension given as JSON text; none for "" or for text that is not JSON. */
export function groupsFromExtensionText(text: string, source: string | undefined): GroupInfo[] {
    if (!text) return [];
    try {
        return groupsFromExtension(JSON.parse(text) as GroupsExtension, source);
    } catch (err) {
        console.warn("[viewport] the model's ADA_EXT_data is not JSON; it has no groups", err);
        return [];
    }
}

/** The groups of the models a viewport announced, by source. */
const announced = new Map<string, GroupInfo[]>();

/** The groups of the announced models that are still on screen, in load order. */
export function announcedGroups(): GroupInfo[] {
    const viewport = activeViewport();
    const out: GroupInfo[] = [];
    for (const [source, groups] of announced) {
        // Forgotten with its model: one that was unloaded, or was the other viewport's.
        if (viewport?.kind !== "native" || viewport.sourceOrigin(source) === null) announced.delete(source);
        else for (const group of groups) out.push(group);
    }
    return out;
}

/** A viewport loaded a model: its groups join the picker's list, or become it. */
export function listAnnouncedGroups(info: LoadedModelInfo): void {
    const had = announced.size > 0;
    if (!info.added) announced.clear();
    // A model with no extension text is not a GLB (an FEA result): its sets reach the store from
    // its manifest, and are not touched here.
    if (info.adaExtData === undefined) return;
    const groups = groupsFromExtensionText(info.adaExtData, info.sourceName || undefined);
    if (groups.length) announced.set(info.sourceName, groups);
    else announced.delete(info.sourceName);
    if (had || announced.size) useSceneInfoStore.getState().setAvailableGroups(announcedGroups());
}

let installed = false;

/** Start listening for models a viewport announces; once per page. */
export function installAnnouncedGroups(): void {
    if (installed) return;
    installed = true;
    onModelLoaded(listAnnouncedGroups);
}
