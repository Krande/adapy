// The camera of the WebGPU embed: the figure's preset framing (the math of adapy's
// ada.visit.rendering.adacpp_offscreen.preset_camera, so the first frame matches the poster) and an
// orbit / pan / zoom around a target, z up as adapy models are.
import type {CameraPreset} from "../embed/index.d";

export type Bounds = [number, number, number, number, number, number];

const DEG = Math.PI / 180;

export class OrbitCamera {
    target: [number, number, number] = [0, 0, 0];
    azimuth = -135 * DEG;
    elevation = 30 * DEG;
    distance = 1;
    fov = 45 * DEG;
    /** The model's bounding radius: near / far hug it, for depth precision. */
    radius = 1;

    /** Frame `bounds` as the preset says (distance "fit": the bounding sphere fills the view). */
    frame(bounds: Bounds, preset: CameraPreset | undefined, aspect: number) {
        const [x0, y0, z0, x1, y1, z1] = bounds;
        this.target = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2];
        this.radius = Math.max(Math.hypot(x1 - x0, y1 - y0, z1 - z0) / 2, 1e-3);
        this.azimuth = (preset?.azimuth_deg ?? -135) * DEG;
        this.elevation = (preset?.elevation_deg ?? 30) * DEG;
        this.fov = (preset?.fov_deg ?? 45) * DEG;
        if (typeof preset?.distance === "number") {
            this.distance = preset.distance;
        } else {
            const margin = preset?.margin ?? 1.15;
            const a = Math.max(aspect, 1e-3);
            const fitV = this.radius / Math.sin(this.fov / 2);
            const fitH = this.radius / Math.sin(Math.atan(Math.tan(this.fov / 2) * a));
            this.distance = Math.max(fitV, fitH) * margin;
        }
    }

    eye(): [number, number, number] {
        const d = this.distance, ce = Math.cos(this.elevation);
        return [
            this.target[0] + d * ce * Math.sin(this.azimuth),
            this.target[1] + d * ce * Math.cos(this.azimuth),
            this.target[2] + d * Math.sin(this.elevation),
        ];
    }

    /** Column-major view and projection matrices (OpenGL clip space, as three.js builds them). */
    matrices(aspect: number): {view: Float32Array; proj: Float32Array} {
        const eye = this.eye();
        const f = norm(sub(this.target, eye));
        let s = cross(f, [0, 0, 1]);
        if (Math.hypot(...s) < 1e-9) s = cross(f, [0, 1, 0]);
        s = norm(s);
        const u = cross(s, f);
        const view = new Float32Array(16);
        view[0] = s[0]; view[4] = s[1]; view[8] = s[2];
        view[1] = u[0]; view[5] = u[1]; view[9] = u[2];
        view[2] = -f[0]; view[6] = -f[1]; view[10] = -f[2];
        view[12] = -dot(s, eye); view[13] = -dot(u, eye); view[14] = dot(f, eye);
        view[15] = 1;
        const near = Math.max(this.distance - this.radius * 1.5, this.distance * 1e-3, 1e-4);
        const far = this.distance + this.radius * 1.5;
        const t = 1 / Math.tan(this.fov / 2);
        const proj = new Float32Array(16);
        proj[0] = t / Math.max(aspect, 1e-3);
        proj[5] = t;
        proj[10] = (far + near) / (near - far);
        proj[11] = -1;
        proj[14] = (2 * far * near) / (near - far);
        return {view, proj};
    }

    orbit(dxPx: number, dyPx: number, heightPx: number) {
        const k = Math.PI / Math.max(heightPx, 1);
        this.azimuth += dxPx * k; // the model's near side follows the pointer
        this.elevation = Math.min(Math.PI / 2 - 1e-3, Math.max(-Math.PI / 2 + 1e-3, this.elevation + dyPx * k));
    }

    /** Move the target in the view plane by a drag of (dx, dy) pixels. */
    pan(dxPx: number, dyPx: number, heightPx: number) {
        const eye = this.eye();
        const f = norm(sub(this.target, eye));
        const s = norm(cross(f, [0, 0, 1]));
        const u = cross(s, f);
        const perPx = (2 * this.distance * Math.tan(this.fov / 2)) / Math.max(heightPx, 1);
        for (let i = 0; i < 3; i++) this.target[i] += (-dxPx * s[i] + dyPx * u[i]) * perPx;
    }

    zoom(factor: number) {
        this.distance = Math.min(this.radius * 200, Math.max(this.radius * 0.05, this.distance * factor));
    }
}

type V3 = [number, number, number];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: V3): V3 => {
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
};
