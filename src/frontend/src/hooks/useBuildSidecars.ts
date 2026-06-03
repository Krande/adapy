import {useEffect, useRef, useState} from "react";
import {ServerFileEntry} from "@/state/serverInfoStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";

export interface GitProvenance {
    commit: string;
    parents: string[];
    branch: string;
    author: string;
    timestamp: string;     // ISO-8601
    remote_url: string;
    is_dirty: boolean;
}

export interface BuildSidecar {
    schema_version: number;
    project_id: string;
    entrypoint: string;
    artefact: string;      // basename of the GLB
    git: GitProvenance;
}

interface CommitDescriptor {
    key: string;            // ``<encodedBranch>/<sha>``
    encodedBranch: string;
    sha: string;
    artefactName: string;   // basename of any one artefact in the commit
}

function extractCommits(files: ServerFileEntry[]): CommitDescriptor[] {
    const seen = new Map<string, CommitDescriptor>();
    for (const f of files) {
        const trimmed = f.name.replace(/^\/+/, "");
        const parts = trimmed.split("/");
        if (parts.length < 4 || parts[0] !== "versions") continue;
        const [, encodedBranch, sha, ...rest] = parts;
        const artefactName = rest.join("/");
        if (artefactName.endsWith(".build.json")) continue;
        const key = `${encodedBranch}/${sha}`;
        if (!seen.has(key)) {
            seen.set(key, {key, encodedBranch, sha, artefactName});
        }
    }
    return Array.from(seen.values());
}

export interface UseBuildSidecarsResult {
    /** commitKey (``<encodedBranch>/<sha>``) → parsed sidecar.
     *  ``null`` entries record a failed fetch so we don't retry every
     *  render. Missing keys mean "not fetched yet". */
    sidecars: ReadonlyMap<string, BuildSidecar | null>;
    /** True while at least one fetch is in flight. */
    loading: boolean;
}

/**
 * Fetch one ``build.json`` sidecar per ``versions/<branch>/<sha>/``
 * group and return them keyed by ``<encodedBranch>/<sha>``.
 *
 * Sidecars carry authoritative git provenance (commit timestamp,
 * branch, parents, author). Sorting by ``git.timestamp`` is correct;
 * sorting by S3 ``LastModified`` is not — re-running CI on an older
 * commit refreshes its mtime and would push it to "latest", which
 * doesn't match what the user pushed.
 *
 * Results cache across renders. When ``files`` grows, only the new
 * commits are fetched; in-flight fetches don't restart.
 */
export function useBuildSidecars(files: ServerFileEntry[]): UseBuildSidecarsResult {
    const scope = scopeUrlPart(useScopeStore.getState().current);
    // Mirror of the rendered map. ``inFlight`` prevents launching a
    // duplicate request for a commit whose first request hasn't
    // resolved yet — without it, the effect re-running while a fetch
    // was in flight would refetch the same key.
    const cacheRef = useRef<Map<string, BuildSidecar | null>>(new Map());
    const inFlightRef = useRef<Set<string>>(new Set());
    const [sidecars, setSidecars] = useState<Map<string, BuildSidecar | null>>(
        () => new Map(),
    );
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        const commits = extractCommits(files);
        const todo = commits.filter(
            (c) => !cacheRef.current.has(c.key) && !inFlightRef.current.has(c.key),
        );
        if (todo.length === 0) {
            if (inFlightRef.current.size === 0) setLoading(false);
            return;
        }
        let cancelled = false;
        setLoading(true);
        for (const c of todo) {
            inFlightRef.current.add(c.key);
            const sidecarKey = `versions/${c.encodedBranch}/${c.sha}/${c.artefactName}.build.json`;
            (async () => {
                let parsed: BuildSidecar | null = null;
                try {
                    const buf = await viewerApi.getBlob(scope, sidecarKey);
                    const text = new TextDecoder().decode(buf);
                    parsed = JSON.parse(text) as BuildSidecar;
                } catch (err) {
                    console.warn(`sidecar fetch failed for ${sidecarKey}:`, err);
                }
                inFlightRef.current.delete(c.key);
                cacheRef.current.set(c.key, parsed);
                if (cancelled) return;
                setSidecars(new Map(cacheRef.current));
                if (inFlightRef.current.size === 0) setLoading(false);
            })();
        }
        return () => {
            cancelled = true;
        };
    }, [files, scope]);

    return {sidecars, loading};
}
