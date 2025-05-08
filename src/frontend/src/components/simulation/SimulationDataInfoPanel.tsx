import React, {useEffect, useState} from 'react';
import {simuluationDataRef} from "../../state/refs";
import {FieldObject} from "../../extensions/sim_metadata";

export default function SimulationDataInfoPanel() {
    const simData = simuluationDataRef.current;
    const [selectedFields, setSelectedFields] = useState<number[]>([]);

    // Initialize selected field indices when simData changes
    useEffect(() => {
        if (simData) {
            setSelectedFields(simData.steps.map(() => 0));
        }
    }, [simData]);

    if (!simData) {
        return (
            <div className="max-w-md mx-auto mt-6 p-4 border rounded-lg shadow-sm">
                <h2 className="text-lg font-semibold text-gray-800">No Simulation Loaded</h2>
                <p className="text-sm text-gray-500 mt-2">Load a GLB with the ADA simulation metadata extension to view details here.</p>
            </div>
        );
    }

    return (
        <div className="max-w-lg p-6 border rounded-lg shadow-sm bg-white bg-opacity-50 max-h-96 overflow-auto">
            <h2 className="text-xl font-semibold text-gray-800">{simData.name}</h2>
            <p className="text-sm text-gray-500 mt-1">{new Date(simData.date).toLocaleString()}</p>

            <div className="mt-4 text-sm text-gray-700">
                Software: <span className="font-medium">{simData.fea_software} {simData.fea_software_version}</span>
            </div>

            <div className="mt-6 space-y-4">
                {simData.steps.map((step, idx) => {
                    const fields = step.fields;
                    const currentIndex = selectedFields[idx] || 0;
                    const field = fields[currentIndex] as FieldObject;

                    const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
                        const newIndex = parseInt(e.target.value, 10);
                        setSelectedFields(prev => {
                            const updated = [...prev];
                            updated[idx] = newIndex;
                            return updated;
                        });
                    };

                    return (
                        <details key={idx} className="border rounded-md">
                            <summary className="px-4 py-2 cursor-pointer select-none text-gray-800 font-medium">
                                Step {idx + 1}: {step.analysis_type}
                            </summary>
                            <div className="px-4 py-4 bg-gray-50 space-y-2">
                                <label className="block text-sm text-gray-700">
                                    Select Field:
                                    <select
                                        className="ml-2 mt-1 p-1 border rounded"
                                        value={currentIndex}
                                        onChange={handleChange}
                                    >
                                        {fields.map((f, fi) => (
                                            <option key={fi} value={fi}>
                                                {f.name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <div className="mt-2 p-2 border rounded bg-white">
                                    <div className="text-sm text-gray-800"><strong>Name:</strong> {field.name}</div>
                                    <div className="text-sm text-gray-800"><strong>Type:</strong> {field.type}</div>
                                    <div className="text-sm text-gray-800">
                                        <strong>BufferView:</strong> {field.data.bufferView}
                                        {field.data.byteOffset !== undefined && (
                                            <span> @ {field.data.byteOffset} bytes</span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </details>
                    );
                })}
            </div>
        </div>
    );
}
