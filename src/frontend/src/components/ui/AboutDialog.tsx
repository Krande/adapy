import React, {useEffect, useState} from "react";

import {PLUGIN_API_VERSION} from "@/plugins/registry";
import {activeUiShellId, getUiShell} from "@/plugins/uiShells";
import {runtime} from "@/runtime/config";
import {buildLabel} from "@/utils/buildLabel";
import {VIEWER_CORE_API_VERSION} from "@/viewer-core";

import {ModalFrame} from "./ModalFrame";

// Help -> About: what this viewer is (its build, its API versions, the UI and the connection),
// what the server reports about itself, and where the documentation is.

export const ABOUT_LINKS: readonly {label: string; href: string}[] = [
    {label: "Documentation", href: "https://krande.github.io/adapy"},
    {label: "Command line", href: "https://krande.github.io/adapy/cli/"},
    {label: "Source code", href: "https://github.com/Krande/adapy"},
    {label: "Report an issue", href: "https://github.com/Krande/adapy/issues"},
];

/** What /api/config says about the server (the fields the dialog shows). */
export interface ServerFacts {
    viewerImageTag?: string | null;
    workerImageTag?: string | null;
    adapyBuildRef?: string | null;
}

export interface AboutInput {
    adapyVersion: string;
    frontendSha: string;
    viewerImageTag: string;
    uniqueVersionId: string | number;
    adapyBuildRef: string;
    rest: boolean;
    apiBase: string;
    websocketOff: boolean;
    websocketPort: number;
    shellLabel: string;
    /** null: not asked (not a REST viewer) or not answered yet; "error": the server did not answer. */
    server: ServerFacts | "error" | null;
}

/** The dialog's rows, as label / value pairs, in order. Pure, for its tests. */
export function aboutRows(i: AboutInput): [string, string][] {
    const rows: [string, string][] = [
        ["Viewer", buildLabel(i.adapyVersion, i.frontendSha, i.viewerImageTag, i.uniqueVersionId, i.adapyBuildRef) || "development build"],
        ["Viewer core API", VIEWER_CORE_API_VERSION],
        ["Plugin API", PLUGIN_API_VERSION],
        ["User interface", i.shellLabel],
        [
            "Connection",
            i.rest ? `Server (REST, ${i.apiBase})` : i.websocketOff ? "None (no server)" : `Local (WebSocket, port ${i.websocketPort})`,
        ],
    ];
    if (i.rest) {
        if (i.server === "error") rows.push(["Server", "did not answer /config"]);
        else if (i.server === null) rows.push(["Server", "asking…"]);
        else {
            const tag = (v?: string | null) => (v || "").trim();
            if (tag(i.server.viewerImageTag)) rows.push(["Server image", tag(i.server.viewerImageTag)]);
            if (tag(i.server.workerImageTag)) rows.push(["Worker image", tag(i.server.workerImageTag)]);
            if (tag(i.server.adapyBuildRef)) rows.push(["Built from", tag(i.server.adapyBuildRef)]);
            if (!tag(i.server.viewerImageTag) && !tag(i.server.workerImageTag) && !tag(i.server.adapyBuildRef)) {
                rows.push(["Server", "no version reported (a development server)"]);
            }
        }
    }
    return rows;
}

function currentInput(server: AboutInput["server"]): AboutInput {
    return {
        adapyVersion: runtime.adapyVersion(),
        frontendSha: runtime.frontendSha(),
        viewerImageTag: runtime.viewerImageTag(),
        uniqueVersionId: runtime.uniqueVersionId(),
        adapyBuildRef: runtime.adapyBuildRef(),
        rest: runtime.isRestMode(),
        apiBase: runtime.apiBase(),
        websocketOff: runtime.websocketDeactivated(),
        websocketPort: runtime.websocketPort(),
        shellLabel: getUiShell(activeUiShellId())?.label ?? activeUiShellId(),
        server,
    };
}

/** The dialog's content, for a given set of facts (its markup tests render this). */
export const AboutContent: React.FC<{input: AboutInput}> = ({input}) => (
    <div className="space-y-3">
        <p className="text-gray-200">adapy viewer: a 3D viewer for structural and FEA models.</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            {aboutRows(input).map(([k, v]) => (
                <React.Fragment key={k}>
                    <dt className="text-gray-400">{k}</dt>
                    <dd className="font-mono break-all">{v}</dd>
                </React.Fragment>
            ))}
        </dl>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {ABOUT_LINKS.map((l) => (
                <li key={l.href}>
                    <a href={l.href} target="_blank" rel="noopener noreferrer" className="text-blue-300 hover:underline">
                        {l.label}
                    </a>
                </li>
            ))}
        </ul>
    </div>
);

const AboutDialog: React.FC<{onClose: () => void}> = ({onClose}) => {
    const rest = runtime.isRestMode();
    const [server, setServer] = useState<AboutInput["server"]>(null);
    useEffect(() => {
        if (!rest) return;
        let live = true;
        fetch(`${runtime.apiBase()}/config`, {cache: "no-store"})
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((cfg: ServerFacts) => live && setServer(cfg))
            .catch(() => live && setServer("error"));
        return () => {
            live = false;
        };
    }, [rest]);
    return (
        <ModalFrame title="About" onClose={onClose} testId="about-dialog">
            <AboutContent input={currentInput(server)} />
        </ModalFrame>
    );
};

export default AboutDialog;
