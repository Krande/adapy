import React from "react";
import {Icon, IconButton, Toolbar, ToolbarSpacer} from "@/components/ui";
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {start_new_node_editor} from "@/utils/node_editor/handlers/start_new_node_editor";
import NodeEditorBody from "./NodeEditorBody";

// The node editor as a dock panel.
//
// Same body as the classic floating window; the react-rnd frame and its hand-styled
// header are gone because the dock supplies both. The two header actions (reload the
// procedure list, pop out a standalone editor) survive as toolbar buttons calling the
// same handlers — they are genuinely useful and would otherwise be lost with the frame.
//
// ReactFlow needs a definite height: it measures its container and renders nothing in an
// auto-height box, which is why the body gets `flex-1 min-h-0` rather than inheriting.

export default function NodeEditorPanel() {
    return (
        <div className="flex flex-col h-full min-h-0">
            <Toolbar label="Node editor" dense className="shrink-0 px-1.5 py-1 border-b border-edge">
                <IconButton
                    size="sm"
                    tooltip="Reload the procedure list from the server"
                    icon={<Icon name="reload" size="sm" />}
                    onClick={() => request_list_of_nodes()}
                />
                <ToolbarSpacer />
                <IconButton
                    size="sm"
                    tooltip="Open a standalone node-editor window"
                    icon={<Icon name="pop-out" size="sm" />}
                    onClick={() => start_new_node_editor()}
                />
            </Toolbar>
            <div className="flex-1 min-h-0">
                <NodeEditorBody />
            </div>
        </div>
    );
}
