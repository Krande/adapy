import {execSync} from "child_process";
import type {Plugin} from "vite";

import {CAPACITY_RESULTS_VERSION} from "./src/services/capacityResultsVersion";

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
                // No git tree (docker/CI building from a copied source). Fall back to the commit
                // the CI runner exposes via env so the hosted bundle still carries a sha.
                const env =
                    process.env.GITHUB_SHA ||
                    process.env.CI_COMMIT_SHA ||
                    process.env.FORGEJO_SHA ||
                    process.env.GIT_SHA ||
                    "";
                sha = env ? env.slice(0, 8) : "";
            }
            const ts = Date.now();
            return html
                .replace(
                    /<!--CAPACITY_RESULTS_VERSION_PLACEHOLDER-->/g,
                    `<meta name="ada-capacity-results-version" content="${CAPACITY_RESULTS_VERSION}">`,
                )
                .replace(
                    /<!--UNIQUE_VERSION_PLACEHOLDER-->/g,
                    `<script>window.UNIQUE_VERSION_ID = ${ts}; window.FRONTEND_SHA = ${JSON.stringify(sha)};</script>`,
                );
        },
    };
}
