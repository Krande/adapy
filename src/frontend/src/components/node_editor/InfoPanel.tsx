// InfoPanel.tsx
import React from 'react';
import { Rnd } from 'react-rnd';

type InfoPanelProps = {
    show: boolean;
    onClose: () => void;
};

const InfoPanel: React.FC<InfoPanelProps> = ({ show, onClose }) => {
    if (!show) return null; // Return null if the panel should not be displayed

    return (
        <Rnd
            default={{
                x: 950,
                y: 100,
                width: 400,
                height: 300,
            }}
            bounds="window"
            style={{
                zIndex: 1001,
                background: 'white',
                border: '1px solid #ccc',
                boxShadow: '0px 0px 10px rgba(0,0,0,0.2)',
            }}
            dragHandleClassName="info-panel-drag-handle"
        >
            {/* Info Panel Header */}
            <div className="info-panel-header info-panel-drag-handle bg-gray-800 text-white px-4 py-2 cursor-move">
                <div className={"flex flex-row"}>
                    <div className={"flex"}>Info Panel</div>
                    <button
                        className={"flex relative bg-red-600 hover:bg-red-600/50 text-white font-bold px-4 ml-auto rounded"}
                        onClick={onClose}
                    >
                        Close
                    </button>
                </div>
            </div>
            {/* Info Panel Content */}
            <div className="info-panel-content p-4">
                <h3 className="text-xl font-bold mb-4">Procedure Information</h3>
                <p className="mb-2">
                    This panel displays detailed information about the selected node or procedure.
                    Use this space to show properties, descriptions, and other metadata.
                </p>
                {/* Add more detailed information here as needed */}
            </div>
        </Rnd>
    );
};

export default InfoPanel;
