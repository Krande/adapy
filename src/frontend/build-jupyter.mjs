import fs from "fs";
import path from "path";

const jupyterDistPath = path.resolve("adapy_viewer_widget/jupyter-dist");

// Ensure the Jupyter build directory exists
if (!fs.existsSync(jupyterDistPath)) {
    fs.mkdirSync(jupyterDistPath, { recursive: true });
}

console.log("✅ Jupyter build completed. Built files are in:", jupyterDistPath);
