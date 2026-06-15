import React from "react";
import {useExperimentalStore} from "@/state/experimentalStore";

const ExperimentalOptions: React.FC = () => {
    const {pyodideConverter, setPyodideConverter} = useExperimentalStore();
    return (
        <div className="space-y-1">
            <label className="flex items-start space-x-2">
                <input
                    type="checkbox"
                    className="mt-1"
                    checked={pyodideConverter}
                    onChange={() => setPyodideConverter(!pyodideConverter)}
                />
                <span className="leading-tight">
                    Convert in-browser (WASM)
                    <span className="block text-xs text-gray-400">
                        Runs STEP / IFC / mesh → GLB conversions client-side instead of on a
                        server worker, off-loading shared infrastructure. Lazy-loads the WASM
                        runtime on first use; unsupported formats still use the server. Off by
                        default.
                    </span>
                </span>
            </label>
        </div>
    );
};

export default ExperimentalOptions;
