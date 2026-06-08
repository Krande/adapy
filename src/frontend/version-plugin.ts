import {execSync} from "child_process";
import type {Plugin} from "vite";

// Replace the <!--UNIQUE_VERSION_PLACEHOLDER--> in index.html with the build id + frontend git
// sha, for EVERY vite config (default / serve / embed). The cloud viewer builds via
// vite.config.serve.ts, which doesn't run embed-script.cjs — so without this the hosted viewer
// showed "Build: 0" and no frontend sha. transformIndexHtml runs on the emitted HTML in all
// build paths, so version info is injected once, consistently.
export function versionInjectPlugin(): Plugin {
    return {
        name: "adapy-version-inject",
        transformIndexHtml(html) {
            let sha = "";
            try {
                sha = execSync("git rev-parse --short HEAD", {stdio: ["ignore", "pipe", "ignore"]})
                    .toString()
                    .trim();
                const dirty =
                    execSync("git status --porcelain", {stdio: ["ignore", "pipe", "ignore"]})
                        .toString()
                        .trim().length > 0;
                if (dirty) sha += "-dirty";
            } catch {
                // not a git tree (e.g. building from a tarball) — leave sha empty
            }
            const ts = Date.now();
            return html.replace(
                /<!--UNIQUE_VERSION_PLACEHOLDER-->/g,
                `<script>window.UNIQUE_VERSION_ID = ${ts}; window.FRONTEND_SHA = ${JSON.stringify(sha)};</script>`,
            );
        },
    };
}
