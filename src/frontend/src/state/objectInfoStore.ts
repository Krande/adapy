import {create} from 'zustand';

type ObjectInfoState = {
    name: string | null;
    setName: (name: string | null) => void;
    faceIndex: number | null;
    setFaceIndex: (faceIndex: number | null) => void;
    clickCoordinate: { x: number; y: number, z: number } | null;
    setClickCoordinate: (clickCoordinate: { x: number; y: number, z: number } | null) => void;
    jsonData: any | null;
    setJsonData: (jsonData: any | null) => void;
    // Source file owning the currently-selected object. Passed back
    // with MESH_INFO_REQUEST so the backend's per-file metadata index
    // can be scoped without ambiguity when multiple files are overlaid.
    fileName: string | null;
    setFileName: (fileName: string | null) => void;
    show_info_box: boolean;
    toggle: () => void;
};

export const useObjectInfoStore = create<ObjectInfoState>((set) => ({
    name: null,
    setName: (name) => set(() => ({name})),
    faceIndex: null,
    setFaceIndex: (faceIndex) => set(() => ({faceIndex})),
    clickCoordinate: null,
    setClickCoordinate: (clickCoordinate) => set(() => ({clickCoordinate})),
    jsonData: null,
    setJsonData: (jsonData) => set(() => ({jsonData})),
    fileName: null,
    setFileName: (fileName) => set(() => ({fileName})),
    show_info_box: false,
    toggle: () => set((state) => ({show_info_box: !state.show_info_box})),
}));
