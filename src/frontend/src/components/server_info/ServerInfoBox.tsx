import React from 'react';
import {PanelCard} from "@/components/ui/PanelFrame";
import ReloadIcon from "../icons/ReloadIcon";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

/** The desktop viewer's server-info content: the file list and its reload. */
export const ServerInfoBody = () => (
    <div className={"flex flex-row"}>
        <div className={"pr-1 "}>Files:</div>
        <select
            id="listbox"
            name="listbox"
            className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold p-2 rounded-sm"
        >
            <option value="item1">Item 1</option>
            <option value="item2">Item 2</option>
            <option value="item3">Item 3</option>
            <option value="item4">Item 4</option>
        </select>
        <button
            className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-2 ml-2 rounded-sm"}
            onClick={() => request_list_of_files_from_server()}
        ><ReloadIcon/></button>
    </div>
);

/** The classic frame: a menu-column card. */
const ServerInfoBox = () => (
    <PanelCard className="w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md" heading="Server Info">
        <ServerInfoBody/>
    </PanelCard>
);

export default ServerInfoBox;
