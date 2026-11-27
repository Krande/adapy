// Asset node builds in the browser: the real `BrowserAssetBuild` the Sources tab's loads use
// (`@/assets/delivery`'s `requestBuild` routes with it).
//
// A capability has a browser path when `/wheels/plugin-wheels-manifest.json` lists a baked wheel
// under that capability's name whose `register` hook registers the builder (an asset builder
// registered by a baked plugin wheel). The build then runs in the Pyodide worker the way a plugin
// job does (`run-plugin-job`, entrypoint `ada.assets.wasm_build:run_job`): the worker installs
// that plugin, reads the node's published files through this scope's blob route, and hands back
// the summary and the files the builder wrote. Those go to the server
// (`POST /assets/build/result`), which composes the build's identity again and validates them
// before storing them where every reader of the node finds them -- the build is shared, as a
// worker's is, not kept in this tab.
//
// Which engine runs is the execution policy's `asset_build` order for the capability (default:
// the browser, then the worker), with worker access applied.

import type { BrowserAssetBuild } from "@/assets/delivery";
import { filesUnderPrefix, type BuildUploadRequest } from "@/assets/buildUpload";
import type { WireBuildAssetResponse } from "@/assets/types";
import { runtime } from "@/runtime/config";
import { assetsApi } from "@/services/api/assets";
import type { ScopeUrl } from "@/services/api/client";
import { getAccessToken } from "@/services/auth/oidc";
import { loadPluginWheelsManifest } from "@/utils/pyodide/pluginWheels";

import { planExecution } from "./executionPolicy";
import { refreshAccessIfStale } from "./workerAccess";

/** `ada.assets.wasm_build.BROWSER_ENTRYPOINT`. */
export const BROWSER_BUILD_ENTRYPOINT = "ada.assets.wasm_build:run_job";

type RunPluginJob = typeof import("@/utils/pyodide/pyodide_converter").runPluginJobViaPyodide;

export interface BrowserAssetBuildOptions {
  /** Injectable for tests: does this viewer bake a wheel registering `capability`? */
  hasWheel?: (capability: string) => Promise<boolean>;
  /** Injectable for tests: the Pyodide run and the upload. */
  runInBrowser?: RunPluginJob;
  upload?: typeof assetsApi.uploadBrowserBuild;
  /** The API base the worker reads through (default: the runtime's). */
  apiBase?: string;
  /** Skip the policy re-read (tests). */
  refresh?: () => Promise<void>;
  onLog?: (msg: string) => void;
}

async function wheelFor(capability: string): Promise<boolean> {
  const manifest = await loadPluginWheelsManifest();
  return Boolean(manifest.plugins[capability]?.wheel);
}

async function defaultRunner(): Promise<RunPluginJob> {
  // Lazily: the Pyodide converter stays out of the bundles that never build in the browser.
  return (await import("@/utils/pyodide/pyodide_converter")).runPluginJobViaPyodide;
}

export function browserAssetBuild(opts: BrowserAssetBuildOptions = {}): BrowserAssetBuild {
  return {
    async route(capability) {
      await (opts.refresh ?? refreshAccessIfStale)();
      const wasm = await (opts.hasWheel ?? wheelFor)(capability);
      const plan = planExecution("asset_build", capability, (e) => e === "pyodide" && wasm, { capability });
      return { engines: plan.engines, browserPath: plan.browserPath };
    },

    async run(scope: string, built: WireBuildAssetResponse, request: BuildUploadRequest, signal?: AbortSignal) {
      const block = built.build;
      if (!block) throw new Error("no build block to run in the browser");
      const token = getAccessToken();
      const runBrowser = opts.runInBrowser ?? (await defaultRunner());
      const res = await runBrowser({
        pluginId: block.capability,
        scope,
        options: block as unknown as Record<string, unknown>,
        derivedPrefix: block.derived_prefix,
        entrypoint: BROWSER_BUILD_ENTRYPOINT,
        apiBase: opts.apiBase ?? runtime.apiBase(),
        authHeaders: token ? { Authorization: `Bearer ${token}` } : undefined,
        onLog: opts.onLog,
      });
      if (signal?.aborted) throw new DOMException("load cancelled", "AbortError");
      const files = filesUnderPrefix(
        block.derived_prefix,
        res.localArtefacts.map((a) => ({ key: a.key, bytes: a.bytes, contentEncoding: a.contentEncoding })),
      );
      // The answer may say `stored: false`: someone built it meanwhile, and theirs is the one read.
      await (opts.upload ?? assetsApi.uploadBrowserBuild)(scope as ScopeUrl, request, res.summary, files);
    },
  };
}
