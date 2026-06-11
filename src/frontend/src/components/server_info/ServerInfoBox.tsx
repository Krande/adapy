import React from 'react';
import {useServerInfoStore} from "@/state/serverInfoStore";
import ReloadIcon from "../icons/ReloadIcon";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

const ServerInfoBox = () => {
    const {} = useServerInfoStore();
    return (
        <div className="bg-gray-900/95 border border-gray-700 text-gray-100 shadow-lg rounded-md p-2 w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md">
            <h2 className="font-bold">Server Info</h2>
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

        </div>
    );
};

export default ServerInfoBox;
