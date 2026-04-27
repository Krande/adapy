import React from "react";
import {takeScreenshot} from "@/utils/takeScreenshot";
import {loadRobot} from "@/utils/robots";
import {debug_print} from "@/utils/debug_print";

const buttonClass =
    "bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full";

const ActionButtons: React.FC = () => (
    <div className="space-y-2">
        <button className={buttonClass} onClick={() => debug_print()}>
            Debug print
        </button>
        <button className={buttonClass} onClick={loadRobot}>
            Load URDF Model
        </button>
        <button
            className={buttonClass}
            onClick={async () => {
                try {
                    await takeScreenshot();
                } catch (error) {
                    console.error("Error taking screenshot:", error);
                }
            }}
        >
            Take Screenshot
        </button>
    </div>
);

export default ActionButtons;
