import React, { useEffect, useState } from 'react';
import { simuluationDataRef } from '../../state/refs';
import type { SimulationDataExtensionMetadata, FieldObject } from '../../extensions/sim_metadata';

export default function SimulationDataInfoPanel() {
  const simData = simuluationDataRef.current as SimulationDataExtensionMetadata | null;
  const [selectedStep, setSelectedStep] = useState(0);
  const [selectedField, setSelectedField] = useState(0);

  // Reset selections when simData changes
  useEffect(() => {
    if (simData) {
      setSelectedStep(0);
      setSelectedField(0);
    }
  }, [simData]);

  if (!simData) {
    return (
      <div className="max-w-md mx-auto mt-6 p-4 border rounded-lg shadow-sm">
        <h2 className="text-lg font-semibold text-gray-800">No Simulation Loaded</h2>
        <p className="text-sm text-gray-500 mt-2">
          Load a GLB with the ADA simulation metadata extension to view details here.
        </p>
      </div>
    );
  }

  const steps = simData.steps;
  const currentStep = steps[selectedStep];
  const fields = currentStep.fields;
  const currentField = fields[selectedField] as FieldObject;

  return (
    <div className="p-6 border rounded-lg shadow-sm bg-white bg-opacity-50 max-h-96 overflow-auto pointer-events-auto">
      <h2 className="text-xl font-semibold text-gray-800">{simData.name}</h2>
      <p className="text-sm text-gray-500 mt-1">
        {new Date(simData.date).toLocaleString()}
      </p>

      <div className="mt-4 text-sm text-gray-700">
        <div>
          <strong>Software:</strong> {simData.fea_software}
        </div>
        <div className="mt-1">
          <strong>Version:</strong> {simData.fea_software_version}
        </div>
      </div>

      <div className="mt-6">
        <div className="flex flex-row gap-4 items-center">
          <label className="flex items-center text-sm text-gray-700">
            <span>Select Step:</span>
            <select
              className="ml-2 p-1 border rounded"
              value={selectedStep}
              onChange={(e) => {
                const idx = parseInt(e.target.value, 10);
                setSelectedStep(idx);
                setSelectedField(0);
              }}
            >
              {steps.map((step, idx) => (
                <option key={idx} value={idx}>
                  Step {idx + 1}: {step.analysis_type}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center text-sm text-gray-700">
            <span>Select Field:</span>
            <select
              className="ml-2 p-1 border rounded"
              value={selectedField}
              onChange={(e) => setSelectedField(parseInt(e.target.value, 10))}
            >
              {fields.map((f, fi) => (
                <option key={fi} value={fi}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-4 p-4 border rounded bg-white">
          <div className="text-sm text-gray-800">
            <strong>Name:</strong> {currentField.name}
          </div>
          <div className="text-sm text-gray-800 mt-1">
            <strong>Type:</strong> {currentField.type}
          </div>
          <div className="text-sm text-gray-800 mt-1">
            <strong>BufferView:</strong> {currentField.data.bufferView}
            {currentField.data.byteOffset !== undefined && (
              <span> @ {currentField.data.byteOffset} bytes</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}