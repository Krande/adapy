// The 3D mouse on a viewport that is not three.js: the device code moves a pose, and the pose is the
// viewport's (ViewportApi.cameraPose / setCameraPose). The three.js viewport drives its own controls
// from its render loop (ThreeCanvas), so this stands by while that one is on screen. It lives on the
// panels' side because the device code imports three.js for its vector maths, which the three.js-free
// viewport's own chunk must not.
import {Vector3} from "three";

import {useSpaceMouseStore} from "@/state/spaceMouseStore";
import {bindSpaceMouseToOptions, tickSpaceMousePose, type SpaceMousePoseRig} from "@/utils/spacemouse/spaceMouseDevice";
import {followActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";

function drive(viewport: ViewportApi): () => void {
    if (viewport.kind === "three") return () => {};
    const unbind = bindSpaceMouseToOptions();
    const rig: SpaceMousePoseRig = {
        worldUp: new Vector3(0, 0, 1),
        fovDeg: 45,
        read(position, target) {
            const pose = viewport.cameraPose();
            if (!pose) return false;
            position.set(pose.eye[0], pose.eye[1], pose.eye[2]);
            target.set(pose.target[0], pose.target[1], pose.target[2]);
            rig.worldUp.set(pose.up[0], pose.up[1], pose.up[2]);
            rig.fovDeg = pose.fovDeg;
            return true;
        },
        write(position, target, roll) {
            viewport.setCameraPose([position.x, position.y, position.z], [target.x, target.y, target.z], roll);
        },
    };
    let frame = 0;
    let last = 0;
    // The device is read once per frame, and only while 3D-mouse support is switched on.
    const loop = (now: number) => {
        frame = requestAnimationFrame(loop);
        const dt = last ? (now - last) / 1000 : 0;
        last = now;
        if (dt > 0) tickSpaceMousePose(dt, rig);
    };
    const follow = (on: boolean) => {
        if (on && !frame) {
            last = 0;
            frame = requestAnimationFrame(loop);
        } else if (!on && frame) {
            cancelAnimationFrame(frame);
            frame = 0;
        }
    };
    follow(useSpaceMouseStore.getState().enabled);
    const unsubscribe = useSpaceMouseStore.subscribe((s, prev) => void (s.enabled !== prev.enabled && follow(s.enabled)));
    return () => {
        unsubscribe();
        follow(false);
        unbind();
    };
}

let installed = false;

/** Start following the viewport on screen; once per page. */
export function installSpaceMousePoseDriver(): void {
    if (installed) return;
    installed = true;
    followActiveViewport(drive);
}
