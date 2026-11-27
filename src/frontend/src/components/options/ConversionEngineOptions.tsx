import React, {useEffect} from "react";

import {hasGlobalWorkerAccess, loadWorkerAccess, useWorkerAccessStore} from "@/services/compute/workerAccess";
import {useConversionEngineStore} from "@/state/conversionEngineStore";

// Where conversions, FEA bakes and plugin compute run. The browser (WASM) is
// the default and needs nothing; the server worker is opt-in, and only offered
// to a user an administrator granted worker pool access.
// One literal: the bundle-provenance check matches bundle strings verbatim
// against src/frontend, and the minifier folds concatenated literals.
const NO_ACCESS_NOTICE =
    "Your account has no worker pool access. Ask an administrator to enable it in the Admin Panel (Users).";
const ConversionEngineOptions: React.FC = () => {
    const {engine, setEngine} = useConversionEngineStore();
    const policy = useWorkerAccessStore((s) => s.policy);
    const loaded = useWorkerAccessStore((s) => s.loaded);
    useEffect(() => {
        void loadWorkerAccess(true);
    }, []);
    const workerAllowed = hasGlobalWorkerAccess(policy);
    const grantedCaps = policy?.allowed_capabilities ?? [];

    return (
        <div className="space-y-2" data-testid="conversion-engine-options">
            <label className="flex items-start space-x-2">
                <input
                    type="radio"
                    name="conversion-engine"
                    className="mt-1"
                    checked={engine === "browser" || !workerAllowed}
                    onChange={() => setEngine("browser")}
                />
                <span className="leading-tight">
                    In-browser (WASM)
                    <span className="block text-xs text-gray-400">
                        Conversions, FEA result bakes and plugin calculations run on this computer.
                        Formats the browser cannot read (e.g. Abaqus .odb) still need the server.
                    </span>
                </span>
            </label>
            <label className={`flex items-start space-x-2 ${workerAllowed ? "" : "opacity-50"}`}>
                <input
                    type="radio"
                    name="conversion-engine"
                    className="mt-1"
                    data-testid="conversion-engine-worker"
                    disabled={!workerAllowed}
                    checked={engine === "worker" && workerAllowed}
                    onChange={() => setEngine("worker")}
                />
                <span className="leading-tight">
                    Server worker pool
                    <span className="block text-xs text-gray-400">
                        {workerAllowed
                            ? "Runs on shared server workers — for long or very large jobs."
                            : !loaded
                              ? "Checking your worker pool access…"
                              : grantedCaps.length > 0
                                ? `Your account may use the worker pool only for: ${grantedCaps.join(", ")}. Everything else runs in the browser.`
                                : NO_ACCESS_NOTICE}
                    </span>
                </span>
            </label>
        </div>
    );
};

export default ConversionEngineOptions;
