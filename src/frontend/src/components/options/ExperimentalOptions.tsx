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
                    Convert IFC in-browser (Pyodide).
                    <span className="block text-xs text-gray-400">
                        Lazy-loads ifcopenshell WASM on first use. Server pipeline still
                        handles other formats.
                    </span>
                </span>
            </label>
        </div>
    );
};

export default ExperimentalOptions;
