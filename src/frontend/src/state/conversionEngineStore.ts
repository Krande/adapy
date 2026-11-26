import {create} from "zustand";
import {persist} from "zustand/middleware";

// Where conversions, FEA bakes and plugin compute run.
//
// "browser" (the default) runs every workload the in-browser WASM/Pyodide
// engine supports client-side; only what it cannot do (e.g. native Abaqus .odb
// export) goes to a server worker. "worker" is an opt-in for long or very large
// jobs and only takes effect for a user an administrator has granted worker
// pool access (Admin Panel → Users) — see `workerPreferred` in
// services/compute/workerAccess.ts. Without that grant the choice is inert and
// the settings panel says so.
//
// This replaced the experimental `pyodideConverter` toggle (default OFF, under
// the "ada-experimental" localStorage key). The new key is deliberate: a
// persisted `false` from the experiment must not opt a user out of the new
// default.
export type ConversionEngine = "browser" | "worker";

interface ConversionEngineState {
    engine: ConversionEngine;
    setEngine: (engine: ConversionEngine) => void;
}

export const useConversionEngineStore = create<ConversionEngineState>()(
    persist(
        (set) => ({
            engine: "browser",
            setEngine: (engine) => set({engine}),
        }),
        {name: "ada-conversion-engine"},
    ),
);
