import React from "react";
import {Switch} from "@/components/ui";
import {useExperimentalStore} from "@/state/experimentalStore";

const ExperimentalOptions: React.FC = () => {
    const {pyodideConverter, setPyodideConverter} = useExperimentalStore();

    // Note: enabling this triggers a background pre-warm of the WASM runtime +
    // CAD stack (see RestModeUI), so the first conversion is instant.
    return (
        <Switch
            label="Convert in-browser (WASM)"
            hint="Runs STEP / IFC / mesh / FEM → GLB (and more) conversions client-side instead of on a server worker, off-loading shared infrastructure. The WASM runtime pre-loads in the background when enabled; unsupported formats still use the server. Off by default."
            checked={pyodideConverter}
            onChange={() => setPyodideConverter(!pyodideConverter)}
        />
    );
};

export default ExperimentalOptions;
