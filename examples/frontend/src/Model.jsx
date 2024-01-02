// src/Model.js
import React, {useState, useEffect} from 'react';
import {useGLTF} from '@react-three/drei';

const Model = ({url, onMeshSelected}) => {
    const {scene} = useGLTF(url, false);
    const [selectedObject, setSelectedObject] = useState(null);

    useEffect(() => {
        if (selectedObject) {
            selectedObject.material.color.set('blue'); // Highlight color
        }
        // Reset color when selection changes
        return () => {
            if (selectedObject) {
                selectedObject.material.color.set(selectedObject.originalColor || 'white');
            }
        };
    }, [selectedObject]);

    const handleClick = (event) => {
        event.stopPropagation();
        if (selectedObject !== event.object) {
            if (selectedObject) {
                selectedObject.material.color.set(selectedObject.originalColor || 'white');
            }
            event.object.originalColor = event.object.material.color.getHex();
            setSelectedObject(event.object);

            const meshInfo = {
                name: event.object.name,
                materialName: event.object.material.name,
                intersectionPoint: event.point,
                faceIndex: event.faceIndex,
                meshClicked: true,
            };

            onMeshSelected(meshInfo);

        }
    };

    return <primitive object={scene} onClick={handleClick} dispose={null}/>;
};

export default Model;
