import assert from "node:assert/strict";
import { test } from "node:test";

// Gallery mode is opt-in on every page load; its walk preferences persist. The stub is installed
// before the store is imported, since the store hydrates at module init.

const backing = new Map<string, string>([
  // As an older build stored it: gallery on, with walk preferences.
  ["ada-gallery", JSON.stringify({ state: { enabled: true, walk: "geoms", geomOrder: "tree", hideUnselected: true }, version: 0 })],
]);
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: (k: string): string | null => backing.get(k) ?? null,
  setItem: (k: string, v: string): void => void backing.set(k, String(v)),
  removeItem: (k: string): void => void backing.delete(k),
  clear: (): void => backing.clear(),
  key: (): string | null => null,
  get length(): number {
    return backing.size;
  },
};

const { useGalleryStore } = await import("@/state/galleryStore");

test("a stored 'on' does not reopen gallery mode; the walk preferences are kept", () => {
  const s = useGalleryStore.getState();
  assert.equal(s.enabled, false);
  assert.equal(s.walk, "geoms");
  assert.equal(s.geomOrder, "tree");
  assert.equal(s.hideUnselected, true);
});

test("turning gallery mode on is not written to storage", () => {
  useGalleryStore.getState().setEnabled(true);
  const stored = JSON.parse(backing.get("ada-gallery") ?? "{}");
  assert.equal("enabled" in (stored.state ?? {}), false);
  useGalleryStore.getState().setEnabled(false);
});
