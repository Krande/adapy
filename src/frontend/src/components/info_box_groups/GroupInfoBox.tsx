import React, { useEffect } from 'react';
import { useGroupInfoStore, GroupInfo } from '../../state/groupInfoStore';
import { adaExtensionRef, sceneRef } from '../../state/refs';
import { useObjectInfoStore } from '../../state/objectInfoStore';
import {selectGroupMembers} from "../../utils/selectGroupMembers";
import {CustomBatchedMesh} from "../../utils/mesh_select/CustomBatchedMesh";



const GroupInfoBox = () => {
    const {
        selectedGroup,
        availableGroups,
        setSelectedGroup,
        setAvailableGroups,
    } = useGroupInfoStore();

    // Collect groups from ADA extension on component mount
    useEffect(() => {
        const collectGroups = () => {
            const groups: GroupInfo[] = [];
            const adaExtension = adaExtensionRef.current;
            
            if (adaExtension) {
                // Collect design groups
                if (adaExtension.design_objects) {
                    adaExtension.design_objects.forEach(designObj => {
                        if (designObj.groups) {
                            designObj.groups.forEach(group => {
                                groups.push({
                                    name: group.name || 'Unnamed Group',
                                    description: group.description,
                                    members: group.members,
                                    type: 'design' as const,
                                    parent_name: designObj.name || 'Unnamed Object'
                                });
                            });
                        }
                    });
                }

                // Collect simulation groups
                if (adaExtension.simulation_objects) {
                    adaExtension.simulation_objects.forEach(simObj => {
                        if (simObj.groups) {
                            simObj.groups.forEach(group => {
                                groups.push({
                                    name: group.name || 'Unnamed Group',
                                    description: group.description,
                                    members: group.members,
                                    type: 'simulation' as const,
                                    parent_name: simObj.name || 'Unnamed Object'
                                });
                            });
                        }
                    });
                }
            }
            
            setAvailableGroups(groups);
        };

        collectGroups();
    }, [setAvailableGroups]);

    const handleGroupSelection = async (event: React.ChangeEvent<HTMLSelectElement>) => {
        const selectedGroupName = event.target.value;
        if (selectedGroupName === '') {
            setSelectedGroup(null);
            useObjectInfoStore.getState().setName('');
            // Clear selection will be handled by selectGroupMembers with empty array
            await selectGroupMembers("", []);
            return;
        } 

        const group = availableGroups.find(g => g.name === selectedGroupName);
        setSelectedGroup(group || null);

        if (group && group.members && group.members.length > 0) {
            // Update object info with group name
            useObjectInfoStore.getState().setName(`Group: ${group.name}`);

            // Select group members in 3D scene
            // find CustomBatchedMeshes in scene
            const customBatchedMeshes: CustomBatchedMesh[] = [];
            sceneRef.current?.traverse(obj => {
                if (obj instanceof CustomBatchedMesh) {
                    customBatchedMeshes.push(obj);
                }
            });
            let mesh_obj = null;
            for (const cbm of customBatchedMeshes) {
                if (cbm.ada_ext_data?.name == group.parent_name){
                    mesh_obj = cbm;
                    break
                }
            }

            if (!mesh_obj) {
                console.warn(`Parent object ${group.parent_name} not found in scene`);
                return;
            }
            await selectGroupMembers(mesh_obj.unique_key, group.members);
        }
    };

    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 min-w-80">
            <h2 className="font-bold">Group Information</h2>
            <div className="table-row pointer-events-auto">
                <div className="table-cell w-24">Group:</div>
                <div className="table-cell w-48">
                    <select 
                        className="w-full p-1 rounded bg-white border"
                        value={selectedGroup?.name || ''}
                        onChange={handleGroupSelection}
                    >
                        <option value="">Select a group...</option>
                        {availableGroups.map((group, index) => (
                            <option key={`${group.name}-${index}`} value={group.name}>
                                {group.name} ({group.type})
                            </option>
                        ))}
                    </select>
                </div>
            </div>
            
            {selectedGroup && (
                <>
                    <div className="table-row">
                        <div className="table-cell w-24">Type:</div>
                        <div className="table-cell w-48 capitalize">
                            {selectedGroup.type} Object
                        </div>
                    </div>
                    
                    <div className="table-row">
                        <div className="table-cell w-24">Description:</div>
                        <div className="table-cell w-48">
                            {selectedGroup.description || 'No description available'}
                        </div>
                    </div>
                    
                    <div className="table-row">
                        <div className="table-cell w-24">Members:</div>
                        <div className="table-cell w-48">
                            {selectedGroup.members ? selectedGroup.members.length : 0}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

export default GroupInfoBox;