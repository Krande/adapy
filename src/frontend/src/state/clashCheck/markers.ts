// Clash group colours and the joint markers drawn in the 3D view.

import type { ClashResult } from "./result";

// ---------------------------------------------------------------------------------------------
// Group colour + the 3D marker plan. A joint is a POINT in the model (its `centre`), and the
// panel is a list of groups; without something drawn at those points a user reading a row has no
// way to tell WHERE in the frame the 48 joints it counts actually are. So the same grouping that
// orders the rows also colours a sphere per joint, and both read the ONE function below -- a row
// whose swatch disagreed with its spheres would be worse than no swatch at all.
// ---------------------------------------------------------------------------------------------

/** A group's colour, as a CSS/THREE-parsable hex string.
 *
 *  Derived from the group's INDEX in `result.groups`, which `group_joints` already sorted
 *  (largest first, then type key) -- so the colour is stable for a given result and needs no
 *  stored palette. Hues walk by the golden angle, which keeps neighbouring rows apart even when a
 *  model produces a dozen groups. */
export function groupColor(result: ClashResult, typeKey: string): string {
  const index = result.groups.findIndex((g) => g.typeKey === typeKey);
  return hueColor(index < 0 ? 0 : index);
}

const GOLDEN_ANGLE_DEG = 137.508;

/** `index -> #rrggbb`, walking hue by the golden angle at a fixed saturation/lightness picked to
 *  read on the dark canvas AND on the panel's dark rows. */
export function hueColor(index: number): string {
  const hue = ((index * GOLDEN_ANGLE_DEG) % 360) / 360;
  const [r, g, b] = hslToRgb(hue, 0.62, 0.58);
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const f = (n: number) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))));
  };
  return [f(0), f(8), f(4)];
}

export interface JointMarker {
  readonly id: string;
  readonly typeKey: string;
  readonly centre: readonly [number, number, number];
  readonly color: string;
  /** Relative size, 1 for a normal marker. A selected group's markers are drawn larger and the
   *  rest smaller, so "which of these am I looking at" is answerable without hiding anything --
   *  hiding the others would lose the only view that shows how a group sits in the whole frame. */
  readonly scale: number;
  /** False for a joint outside the selected group: the caller dims it rather than dropping it. */
  readonly emphasised: boolean;
  /** The one joint the cursor is on, if any -- drawn largest, so arrowing down the list moves a
   *  visible dot through the model. */
  readonly focused: boolean;
}

/** One marker per joint, in result order, coloured by group.
 *
 *  `visibleTypeKeys` is the set of groups the panel's FILTERS currently leave on screen -- a
 *  marker for a row the user has filtered away would contradict the list beside it. `null` means
 *  no filter is active (every group shows). `highlight` is the expanded group, if any. */
export function jointMarkers(
  result: ClashResult,
  opts?: { visibleTypeKeys?: ReadonlySet<string> | null; highlight?: string | null; focus?: string | null },
): readonly JointMarker[] {
  const visible = opts?.visibleTypeKeys ?? null;
  const highlight = opts?.highlight ?? null;
  const focus = opts?.focus ?? null;
  const out: JointMarker[] = [];
  for (const joint of result.joints) {
    if (visible && !visible.has(joint.typeKey)) continue;
    const emphasised = highlight === null || joint.typeKey === highlight;
    const focused = focus !== null && joint.id === focus;
    out.push({
      id: joint.id,
      typeKey: joint.typeKey,
      centre: joint.centre,
      color: groupColor(result, joint.typeKey),
      scale: focused ? 2.4 : highlight === null ? 1 : emphasised ? 1.6 : 0.6,
      emphasised: emphasised || focused,
      focused,
    });
  }
  return out;
}
