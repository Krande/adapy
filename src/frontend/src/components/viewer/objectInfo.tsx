import React from 'react';
import {useObjectInfoStore} from '../../state/objectInfoStore';

const ObjectInfoBox = () => {
    const {name, faceIndex} = useObjectInfoStore();

    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 m-2">
            <h2 className={"font-bold"}>Selected Object Info</h2>
            <div className="table-row">
                <div className="table-cell w-24">Name:</div>
                <div className="table-cell w-48">{name}</div>
            </div>
            <div className="table-row">
                <div className="table-cell w-24">Face Index:</div>
                <div className="table-cell w-48">{faceIndex}</div>
            </div>
        </div>
    );
};

export default ObjectInfoBox;