import React from "react";
import {Button} from "@/components/ui";
import {takeScreenshot} from "@/utils/takeScreenshot";
import {loadRobot} from "@/utils/robots";
import {debug_print} from "@/utils/debug_print";

// Re-chromed. These are diagnostic/utility actions, not the panel's main affordance, so
// they are `secondary` rather than the three identical accent-blue buttons they were —
// three primary buttons in a row give no clue which one you actually want.

const ActionButtons: React.FC = () => (
    <div className="flex flex-col gap-2">
        <Button variant="secondary" block onClick={() => debug_print()}>
            Debug print
        </Button>
        <Button variant="secondary" block onClick={loadRobot}>
            Load URDF model
        </Button>
        <Button
            variant="secondary"
            block
            onClick={async () => {
                try {
                    await takeScreenshot();
                } catch (error) {
                    console.error("Error taking screenshot:", error);
                }
            }}
        >
            Take screenshot
        </Button>
    </div>
);

export default ActionButtons;
