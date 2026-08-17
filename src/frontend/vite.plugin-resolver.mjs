// Generic resolver for build-time plugin packages: maps any bare import of
// `@adapy-plugins/<name>` to `packages/plugins/<name>/src/register.tsx`.
//
// This replaces per-package Vite aliases so core config names NO plugin — a
// package (built-in like `demo`, or one overlaid into packages/plugins/ at build
// time) resolves purely by its location. Shared by all three Vite configs
// (serve/embed/default). See scripts/gen-plugin-registry.mjs + plugins.json for
// which packages are actually registered.
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @returns {import('vite').Plugin} */
export function adapyPluginsResolver() {
  const prefix = "@adapy-plugins/";
  return {
    name: "adapy-plugins-resolver",
    enforce: "pre",
    resolveId(id) {
      if (!id.startsWith(prefix)) return null;
      const name = id.slice(prefix.length);
      // Only a bare package specifier (no deep subpath) maps to its entry.
      if (!name || name.includes("/")) return null;
      return path.resolve(__dirname, `packages/plugins/${name}/src/register.tsx`);
    },
  };
}
