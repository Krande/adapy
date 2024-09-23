import React, { useState } from 'react';
import { JsonView, collapseAllNested } from 'react-json-view-lite';
import { Rnd } from 'react-rnd';
import 'react-json-view-lite/dist/index.css';

type JsonViewerProps = {
  data: any;
};

const JsonViewerComponent: React.FC<JsonViewerProps> = ({ data }) => {
  const [isDocked, setIsDocked] = useState(true);

  const toggleDock = () => {
    setIsDocked(!isDocked);
  };

  const dockedViewer = (
    <div className="json-viewer bg-white p-2 border rounded">
      <div className="flex justify-between items-center mb-2">
        <h3 className="font-bold">JSON Data</h3>
        <button
          className="text-blue-500 underline"
          onClick={toggleDock}
        >
          Undock
        </button>
      </div>
      <JsonView data={data} shouldExpandNode={collapseAllNested} />
    </div>
  );

  const undockedViewer = (
    <Rnd
      default={{
        x: 100,
        y: 100,
        width: 400,
        height: 300,
      }}
      minWidth={200}
      minHeight={100}
      bounds="window"
      className="bg-white border rounded shadow-lg"
    >
      <div className="json-viewer p-2 h-full">
        <div className="flex justify-between items-center mb-2">
          <h3 className="font-bold">JSON Data</h3>
          <button
            className="text-blue-500 underline"
            onClick={toggleDock}
          >
            Dock
          </button>
        </div>
        <div className="overflow-auto h-full">
          <JsonView data={data} shouldExpandNode={collapseAllNested} />
        </div>
      </div>
    </Rnd>
  );

  return isDocked ? dockedViewer : undockedViewer;
};

export default JsonViewerComponent;
