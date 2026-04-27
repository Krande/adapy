// Main-thread driver for the Pyodide IFC -> GLB experiment.
// Spawns the worker once on page load, forwards file bytes for
// conversion, and surfaces logs / stats / download.

const log = document.getElementById("log");
const fileInput = document.getElementById("ifc-input");
const convertBtn = document.getElementById("convert-btn");
const downloadBtn = document.getElementById("download-btn");
const stats = document.getElementById("stats");

let glbBlob = null;
let glbName = "output.glb";
let bootStart = performance.now();
let convertStart = 0;
let workerReady = false;

function logLine(msg, cls) {
    const line = document.createElement("div");
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    if (cls) line.classList.add(cls);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

logLine("Spawning worker…");
// classic worker so importScripts() works for the Pyodide CDN bootstrap.
const worker = new Worker("worker.js");

worker.onmessage = (e) => {
    const data = e.data;
    if (data.type === "log") {
        logLine(data.message);
    } else if (data.type === "ready") {
        workerReady = true;
        const elapsed = ((performance.now() - bootStart) / 1000).toFixed(1);
        logLine(`Pyodide + packages ready (${elapsed}s)`);
        stats.textContent = `Boot: ${elapsed}s. Pick an IFC file to convert.`;
        convertBtn.disabled = !fileInput.files.length;
    } else if (data.type === "result") {
        const elapsed = ((performance.now() - convertStart) / 1000).toFixed(1);
        glbBlob = new Blob([data.bytes], {type: "model/gltf-binary"});
        downloadBtn.disabled = false;
        const sizeKb = (data.bytes.byteLength / 1024).toFixed(1);
        stats.textContent = `Converted to ${sizeKb} KB GLB in ${elapsed}s.`;
        logLine(stats.textContent);
        convertBtn.disabled = false;
    } else if (data.type === "error") {
        logLine(`ERROR: ${data.message}`, "err");
        stats.textContent = "Conversion failed — see log.";
        stats.classList.add("err");
        convertBtn.disabled = false;
    }
};

worker.onerror = (e) => {
    logLine(`Worker error: ${e.message}`, "err");
};

fileInput.addEventListener("change", () => {
    convertBtn.disabled = !workerReady || !fileInput.files.length;
});

convertBtn.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    convertBtn.disabled = true;
    downloadBtn.disabled = true;
    stats.classList.remove("err");
    stats.textContent = "Converting…";
    glbName = file.name.replace(/\.ifc$/i, ".glb");
    logLine(`Reading ${file.name} (${(file.size / 1024).toFixed(1)} KB)…`);
    const buf = await file.arrayBuffer();
    convertStart = performance.now();
    // Transfer the buffer so we don't double-allocate in the worker.
    worker.postMessage({type: "convert", bytes: buf, filename: file.name}, [buf]);
});

downloadBtn.addEventListener("click", () => {
    if (!glbBlob) return;
    const url = URL.createObjectURL(glbBlob);
    const a = document.createElement("a");
    a.href = url;
    a.download = glbName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
});
