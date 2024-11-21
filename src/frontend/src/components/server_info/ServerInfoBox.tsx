import React from 'react';
import {useServerInfoStore} from "../../state/serverInfoStore";
import ReloadIcon from "../icons/ReloadIcon";
import {request_list_of_files_from_server} from "../../utils/server_info/comms/request_list_of_files_from_server";

const ServerInfoBox = () => {
    const {} = useServerInfoStore();
    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 mt-2 ml-2 mr-2 min-w-80">
            <h2 className="font-bold">Server Info</h2>
            <div className={"flex flex-row"}>
                <div className={"pr-1 "}>Files:</div>
                <select
                    id="listbox"
                    name="listbox"
                    className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold p-2 rounded"
                >
                    <option value="item1">Item 1</option>
                    <option value="item2">Item 2</option>
                    <option value="item3">Item 3</option>
                    <option value="item4">Item 4</option>
                </select>
                <button
                    className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-2 ml-2 rounded"}
                    onClick={() => request_list_of_files_from_server()}
                ><ReloadIcon/></button>
            </div>

        </div>
    );
};

export default ServerInfoBox;
