// Whether the demo plugin is enabled for this session. It ships compiled into
// the bundle (build-time registry) but stays DORMANT by default so existing
// users see zero change — it activates only when explicitly opted in via:
//   - `globalThis.__ADA_DEMO_PLUGIN__ === true` (test / embed hook), OR
//   - a `?plugins=demo` (comma list) query param, OR
//   - localStorage key `ada:plugins` containing "demo".
export function isDemoEnabled(): boolean {
  try {
    if ((globalThis as Record<string, unknown>).__ADA_DEMO_PLUGIN__ === true) return true;
    if (typeof window !== "undefined" && window.location) {
      const raw = new URLSearchParams(window.location.search).get("plugins") ?? "";
      if (raw.split(",").map((s) => s.trim()).includes("demo")) return true;
    }
    if (typeof localStorage !== "undefined") {
      const raw = localStorage.getItem("ada:plugins") ?? "";
      if (raw.split(",").map((s) => s.trim()).includes("demo")) return true;
    }
  } catch {
    // Any access failure (no DOM, storage disabled) => not enabled.
  }
  return false;
}
