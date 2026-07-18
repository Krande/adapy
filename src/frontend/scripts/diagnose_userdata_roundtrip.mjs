// Diagnose whether scene.userData round-trips through
// GLTFExporter → GLTFLoader. Mirrors the assembleFeaGlb code path:
//   1. Build a Group with a child Mesh named "node0"
//   2. Pin scene.userData["draw_ranges_node0"] + scene.userData["id_hierarchy"]
//   3. Export to binary GLB
//   4. Parse the JSON chunk and dump sceneDef.extras
//   5. Re-load via GLTFLoader and dump scene.userData + mesh.name
//
// Run from the frontend directory:
//   node --experimental-fetch scripts/diagnose_userdata_roundtrip.mjs

import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"

// Minimal DOM stubs for three.js's loader. parseAsync(buffer, '') doesn't
// touch fetch/URL, but the loader's constructor reads some globals.
globalThis.self = globalThis
globalThis.window = globalThis

// FileReader polyfill — Node 18's Blob exists but FileReader doesn't
// (it does in Node 20+). The exporter uses FileReader synchronously
// inside its async pipeline via onloadend.
class NodeFileReader {
    readAsArrayBuffer(blob) {
        blob.arrayBuffer().then((buf) => {
            this.result = buf
            this.onloadend?.()
        })
    }
    readAsDataURL(blob) {
        blob.arrayBuffer().then((buf) => {
            const b64 = Buffer.from(buf).toString("base64")
            this.result = `data:${blob.type};base64,${b64}`
            this.onloadend?.()
        })
    }
}
globalThis.FileReader = NodeFileReader

// --- 1. Build a tiny scene that mirrors assembleFeaGlb's output shape -------
// `scene` simulates what GLTFLoader hands back: a THREE.Group, NOT a
// THREE.Scene. The fix reparents into a real THREE.Scene before export
// so the userData lands on sceneDef.extras instead of inner-node extras.
const loadedGroup = new THREE.Group()

const positions = new Float32Array([
    0, 0, 0,
    1, 0, 0,
    0, 1, 0,
    1, 1, 0,
])
const indices = new Uint32Array([0, 1, 2, 1, 3, 2])
const geom = new THREE.BufferGeometry()
geom.setAttribute("position", new THREE.BufferAttribute(positions, 3))
geom.setIndex(new THREE.BufferAttribute(indices, 1))

// Install a morph target (displacement delta) to mirror the FEA bake.
const displacement = new Float32Array([
    0, 0, 0,
    0, 0, 0.5,
    0, 0, 0.5,
    0, 0, 0,
])
geom.morphAttributes.position = [new THREE.BufferAttribute(displacement, 3)]
geom.morphTargetsRelative = true

const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial())
mesh.name = "node0"
mesh.morphTargetInfluences = [1.0]
mesh.morphTargetDictionary = { displacement: 0 }
loadedGroup.add(mesh)

// Apply the assembleFeaGlb fix: reparent into a real THREE.Scene
const scene = new THREE.Scene()
while (loadedGroup.children.length > 0) {
    scene.add(loadedGroup.children[0])
}

scene.userData["draw_ranges_node0"] = {
    E1: [0, 3],
    E2: [3, 3],
}
scene.userData["id_hierarchy"] = {
    "fea-elements-root": ["FEA elements", "*"],
    E1: ["E1", "fea-elements-root"],
    E2: ["E2", "fea-elements-root"],
}

console.log("=== BEFORE EXPORT ===")
console.log("mesh.name:", mesh.name)
console.log("scene.userData keys:", Object.keys(scene.userData))

// --- 2. Export to binary GLB ------------------------------------------------
const exporter = new GLTFExporter()
const result = await new Promise((resolve, reject) => {
    exporter.parse(
        scene,
        (r) => resolve(r),
        (err) => reject(err),
        { binary: true },
    )
})

const bytes = new Uint8Array(result)
console.log("\n=== GLB BYTES ===")
console.log("byteLength:", bytes.byteLength)

// --- 3. Parse the JSON chunk manually ---------------------------------------
const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
const magic = dv.getUint32(0, true)
const version = dv.getUint32(4, true)
const totalLen = dv.getUint32(8, true)
const jsonChunkLen = dv.getUint32(12, true)
const jsonChunkType = dv.getUint32(16, true)
const jsonStart = 20
const jsonStr = new TextDecoder().decode(
    bytes.subarray(jsonStart, jsonStart + jsonChunkLen),
)
const gltfJson = JSON.parse(jsonStr)

console.log("\n=== GLB JSON ===")
console.log("magic:", magic.toString(16), "version:", version)
console.log("scenes:", JSON.stringify(gltfJson.scenes, null, 2))
console.log("nodes:", JSON.stringify(gltfJson.nodes, null, 2))

// --- 4. Round-trip via GLTFLoader ------------------------------------------
const loader = new GLTFLoader()
const gltf = await loader.parseAsync(bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
), "")

console.log("\n=== AFTER LOAD ===")
console.log("gltf.scene.userData keys:", Object.keys(gltf.scene.userData))
console.log(
    "draw_ranges_node0:",
    JSON.stringify(gltf.scene.userData["draw_ranges_node0"]),
)
console.log(
    "id_hierarchy:",
    JSON.stringify(gltf.scene.userData["id_hierarchy"]),
)

let foundMesh = null
gltf.scene.traverse((o) => {
    if (o.isMesh && !foundMesh) foundMesh = o
})
console.log("loaded mesh.name:", foundMesh?.name)
console.log(
    "loaded morphTargetInfluences:",
    JSON.stringify(foundMesh?.morphTargetInfluences),
)
console.log(
    "loaded morphAttributes.position[0]?:",
    !!foundMesh?.geometry?.morphAttributes?.position?.[0],
)
console.log(
    "loaded morphTargetsRelative:",
    foundMesh?.geometry?.morphTargetsRelative,
)

// --- 5. Verdict -------------------------------------------------------------
const drawOk = !!gltf.scene.userData["draw_ranges_node0"]
const hierOk = !!gltf.scene.userData["id_hierarchy"]
const nameOk = foundMesh?.name === "node0"
const morphOk = !!foundMesh?.geometry?.morphAttributes?.position?.[0]
const weightOk = foundMesh?.morphTargetInfluences?.[0] === 1.0
console.log("\n=== VERDICT ===")
console.log("draw_ranges_node0 round-trip:", drawOk)
console.log("id_hierarchy round-trip:    ", hierOk)
console.log("mesh.name === 'node0':       ", nameOk)
console.log("morph attribute present:     ", morphOk)
console.log("morph weight = 1.0:          ", weightOk)
process.exit(drawOk && hierOk && nameOk && morphOk && weightOk ? 0 : 1)
