import fs from "fs";
import path from "path";

const jupyterDistPath = path.resolve("adapy_viewer_widget/jupyter-dist/assets");

// Ensure the Jupyter build directory exists
if (!fs.existsSync(jupyterDistPath)) {
    console.error("❌ Jupyter build directory not found. Please run this script in a conda environment.");
}

// Get the env var for conda PREFIX path
console.log("✅ Jupyter build completed. Built files are in:", jupyterDistPath);
