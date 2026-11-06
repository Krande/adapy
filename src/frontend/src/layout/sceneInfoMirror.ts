// The Scene panel in a pop-out window: `core:scene-info`.
//
// What the panel's Model tab shows of the main window's scene, as plain data: the loaded
// models and whether each is shown, the groups they carry, the take-off, and the selection
// boxes. A pop-out puts it into its own copy of the stores (the model list, the scene info
// store, the stats store, the box store), so the panel's components render as they do in
// the main window; what the panel DOES goes back as scene actions (`./sceneActions`:
// `core:model-visible`, `core:unload-models`, `core:select-group`, `core:box-*`).
//
// Limits: the groups' member lists never cross (a group carries `member_count` instead), and
// at most `SCENE_INFO_GROUP_CAP` groups are sent (`groupsTotal` says how many there are); the
// groups go only when they change. The panel's other tabs (overlays, tools, clip, mesh,
// clashes, joints) and its saved groups, source and face-picking sections read or change the
// scene itself and stay in the main window.

import {useBBoxSelectionStore, type SelectionBox} from "@/state/bboxSelectionStore";
import {useModelState} from "@/state/modelState";
import {loadedSourceGroups} from "@/state/modelSession";
import {useSceneInfoStore, type GroupInfo} from "@/state/sceneInfoStore";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {useStatsStore} from "@/state/statsStore";
import type {ModelStats} from "@/utils/stats/modelStats";

import {onSceneInfoChanged} from "./sceneInfoSignal";
import {registerMirrorTopic, type MirrorTopic} from "./windowMirror";

export const SCENE_INFO_TOPIC = "core:scene-info";

/** Most groups one snapshot carries. */
export const SCENE_INFO_GROUP_CAP = 5000;

export type MirroredGroup = Omit<GroupInfo, "members"> & {member_count: number};

export interface SceneInfoSnapshot {
    /** The loaded models' source names, in load order, and whether each is shown (absent: no scene group to flip). */
    models: {name: string; visible: boolean | null}[];
    selectedGroup: MirroredGroup | null;
    stats: {loading: boolean; available: boolean; stats: ModelStats | null};
    boxes: SelectionBox[];
    activeBoxId: string | null;
    /** Sent when it changes (and to a window that asks). */
    groups?: MirroredGroup[];
    groupsTotal?: number;
}

const mirroredGroup = (g: GroupInfo): MirroredGroup => {
    const {members, ...rest} = g;
    return {...rest, member_count: members?.length ?? 0};
};

export function sceneInfoSnapshot(withGroups = true): SceneInfoSnapshot {
    const info = useSceneInfoStore.getState();
    const stats = useStatsStore.getState();
    const boxes = useBBoxSelectionStore.getState();
    const out: SceneInfoSnapshot = {
        models: [...useModelState.getState().loadedSourceNames].map((name) => {
            const g = loadedSourceGroups.get(name);
            return {name, visible: g ? g.visible : null};
        }),
        selectedGroup: info.selectedGroup ? mirroredGroup(info.selectedGroup) : null,
        stats: {loading: stats.loading, available: stats.available, stats: stats.stats},
        boxes: boxes.boxes,
        activeBoxId: boxes.activeId,
    };
    if (withGroups) {
        out.groups = info.availableGroups.slice(0, SCENE_INFO_GROUP_CAP).map(mirroredGroup);
        out.groupsTotal = info.availableGroups.length;
    }
    return out;
}

let gotGroups = false;

/** Put a snapshot into this window's stores. False: a message without groups before the first with them. */
export function applySceneInfo(data: SceneInfoSnapshot): boolean {
    if (!data || typeof data !== "object") return false;
    if (!data.groups && !gotGroups) return false;
    useModelState.setState({loadedSourceNames: new Set(data.models.map((m) => m.name))});
    const visible: Record<string, boolean> = {};
    for (const m of data.models) if (m.visible !== null) visible[m.name] = m.visible;
    useSceneMirrorStore.setState({modelVisible: visible});
    const next: Partial<ReturnType<typeof useSceneInfoStore.getState>> = {selectedGroup: data.selectedGroup as GroupInfo | null};
    if (data.groups) {
        next.availableGroups = data.groups as unknown as GroupInfo[];
        gotGroups = true;
        useSceneMirrorStore.setState({groupsTotal: data.groupsTotal ?? data.groups.length});
    }
    useSceneInfoStore.setState(next);
    useStatsStore.setState({loading: data.stats.loading, available: data.stats.available, stats: data.stats.stats});
    useBBoxSelectionStore.setState({boxes: data.boxes, activeId: data.activeBoxId});
    return true;
}

export function sceneInfoTopic(): MirrorTopic<SceneInfoSnapshot> {
    let sentGroups: unknown = undefined;
    return {
        id: SCENE_INFO_TOPIC,
        snapshot: () => {
            sentGroups = useSceneInfoStore.getState().availableGroups;
            return sceneInfoSnapshot(true);
        },
        delta: () => {
            const groups = useSceneInfoStore.getState().availableGroups;
            if (groups === sentGroups) return sceneInfoSnapshot(false);
            sentGroups = groups;
            return sceneInfoSnapshot(true);
        },
        apply: applySceneInfo,
        subscribe(cb) {
            const offs = [
                useModelState.subscribe((s, p) => void (s.loadedSourceNames !== p.loadedSourceNames && cb())),
                useSceneInfoStore.subscribe((s, p) => void ((s.availableGroups !== p.availableGroups || s.selectedGroup !== p.selectedGroup) && cb())),
                useStatsStore.subscribe((s, p) => void ((s.stats !== p.stats || s.loading !== p.loading || s.available !== p.available) && cb())),
                useBBoxSelectionStore.subscribe((s, p) => void ((s.boxes !== p.boxes || s.activeId !== p.activeId) && cb())),
                onSceneInfoChanged(cb),
            ];
            return () => offs.forEach((off) => off());
        },
    };
}

let registered = false;

/** Register `core:scene-info` (once per page). */
export function registerSceneInfoTopic(): void {
    if (registered) return;
    registered = true;
    registerMirrorTopic(sceneInfoTopic());
}

/** Forget the registration -- tests only (with `resetMirror`). */
export function resetSceneInfoTopic(): void {
    registered = false;
    gotGroups = false;
}
