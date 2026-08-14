import * as THREE from "three";

// Factorio-style billboard icons for the type-overlay: each icon is a small
// canvas drawn once into a CanvasTexture on a camera-facing THREE.Sprite. Icons
// are non-pickable (layer 1), always-visible (depthTest off, high renderOrder)
// so they float above the geometry like the FEM glyph overlay.

const CANVAS = 128;

function makeSprite(
  draw: (ctx: CanvasRenderingContext2D, s: number) => void,
): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = CANVAS;
  canvas.height = CANVAS;
  const ctx = canvas.getContext("2d")!;
  draw(ctx, CANVAS);
  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 4;
  const mat = new THREE.SpriteMaterial({
    map: tex,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(mat);
  sprite.layers.set(1); // non-pickable overlay
  sprite.renderOrder = 9995;
  return sprite;
}

function roundedDisk(
  ctx: CanvasRenderingContext2D,
  s: number,
  fill: string,
  stroke = "#ffffff",
) {
  ctx.beginPath();
  ctx.arc(s / 2, s / 2, s * 0.42, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = s * 0.06;
  ctx.strokeStyle = stroke;
  ctx.stroke();
}

function centeredGlyph(
  ctx: CanvasRenderingContext2D,
  s: number,
  glyph: string,
  color: string,
) {
  ctx.fillStyle = color;
  ctx.font = `bold ${s * 0.5}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, s / 2, s * 0.54);
}

/** A colored disk with a glyph/letter — used for equipment (P pump, T tank,
 * ⚡ electrical, ⚙ other). */
export function diskSprite(
  glyph: string,
  bg: string,
  fg = "#ffffff",
): THREE.Sprite {
  return makeSprite((ctx, s) => {
    roundedDisk(ctx, s, bg);
    centeredGlyph(ctx, s, glyph, fg);
  });
}

/** A colored teardrop — a fluid medium marker (blue water, black oil). */
export function dropSprite(color: string): THREE.Sprite {
  return makeSprite((ctx, s) => {
    const cx = s / 2;
    ctx.beginPath();
    ctx.moveTo(cx, s * 0.12);
    ctx.bezierCurveTo(s * 0.9, s * 0.55, s * 0.68, s * 0.9, cx, s * 0.9);
    ctx.bezierCurveTo(s * 0.32, s * 0.9, s * 0.1, s * 0.55, cx, s * 0.12);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = s * 0.05;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  });
}

/** A yellow lightning bolt — electrical service / production. */
export function boltSprite(color = "#f5c518"): THREE.Sprite {
  return makeSprite((ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s * 0.58, s * 0.08);
    ctx.lineTo(s * 0.28, s * 0.55);
    ctx.lineTo(s * 0.47, s * 0.55);
    ctx.lineTo(s * 0.4, s * 0.92);
    ctx.lineTo(s * 0.74, s * 0.42);
    ctx.lineTo(s * 0.53, s * 0.42);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = s * 0.045;
    ctx.strokeStyle = "#5c4a00";
    ctx.stroke();
  });
}

/** A red warning badge with "!" — equipment with unconnected input ports. */
export function warnSprite(): THREE.Sprite {
  return makeSprite((ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s / 2, s * 0.1);
    ctx.lineTo(s * 0.92, s * 0.86);
    ctx.lineTo(s * 0.08, s * 0.86);
    ctx.closePath();
    ctx.fillStyle = "#e02424";
    ctx.fill();
    ctx.lineWidth = s * 0.05;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    centeredGlyph(ctx, s, "!", "#ffffff");
  });
}

export function disposeSprite(sprite: THREE.Sprite) {
  const mat = sprite.material as THREE.SpriteMaterial;
  mat.map?.dispose();
  mat.dispose();
}
