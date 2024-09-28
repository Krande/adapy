import React from 'react';
import { useObjectInfoStore } from '../../state/objectInfoStore';
import JsonViewerComponent from './JsonViewerComponent';

const ObjectInfoBox = () => {
  const {
    name,
    faceIndex,
    jsonData,
    isJsonViewVisible,
    setIsJsonViewVisible,
  } = useObjectInfoStore();

  const toggleJsonView = () => {
    setIsJsonViewVisible(!isJsonViewVisible);
  };

  return (
    <div className="bg-gray-400 bg-opacity-50 rounded p-2 m-2 min-w-80">
      <h2 className="font-bold">Selected Object Info</h2>
      <div className="table-row">
        <div className="table-cell w-24">Name:</div>
        <div className="table-cell w-48">{name}</div>
      </div>
      <div className="table-row">
        <div className="table-cell w-24">Face Index:</div>
        <div className="table-cell w-48">{faceIndex}</div>
      </div>
      {jsonData && (
        <div className="table-row">
          <div className="table-cell w-24">JSON Data:</div>
          <div className="table-cell w-48">
            <button
              className="bg-blue-500 text-white px-2 py-1 rounded"
              onClick={toggleJsonView}
            >
              {isJsonViewVisible ? 'Hide JSON' : 'Show JSON'}
            </button>
          </div>
        </div>
      )}
      {isJsonViewVisible && jsonData && (
        <div className="mt-2">
          <JsonViewerComponent data={jsonData} />
        </div>
      )}
    </div>
  );
};

export default ObjectInfoBox;
