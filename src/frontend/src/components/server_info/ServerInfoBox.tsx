import React from 'react';
import {useServerInfoStore} from "../../state/serverInfoStore";

const ServerInfoBox = () => {
    const {} = useServerInfoStore();
    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 m-2 min-w-80">
            <select
                id="listbox"
                name="listbox"
                className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"
            >
                <option value="item1">Item 1</option>
                <option value="item2">Item 2</option>
                <option value="item3">Item 3</option>
                <option value="item4">Item 4</option>
            </select>
        </div>
    );
};

export default ServerInfoBox;
