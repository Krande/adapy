export function loadGLTFfrombase64(base64_raw_data: string): string {
    console.log("Loading GLTF from base64 data");
    const base64GLB = "data:text/plain;base64," + base64_raw_data

    // Decode Base64
    const base64Data = base64GLB.split(",")[1];
    const binary = atob(base64Data);
    const buffer = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      buffer[i] = binary.charCodeAt(i);
    }

    // Create Blob & URL
    const blob = new Blob([buffer], { type: "model/gltf-binary" });
    return URL.createObjectURL(blob);
}