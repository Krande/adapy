@echo off

setlocal enabledelayedexpansion

echo "Building frontend"

cd src/frontend
npm install
if errorlevel 1 exit 1
npm run build-jupyter
if errorlevel 1 exit 1
:: /share/jupyter/labextensions/@my-org/my-package
set DEST=%PREFIX%\share\jupyter\labextensions\ada-py-viewer
mkdir %DEST%

echo "Copying files to %DEST%"
xcopy /s /y adapy_viewer_widget\jupyter-dist %DEST%
if errorlevel 1 exit 1
xcopy /s /y adapy_viewer_widget\install.json %DEST%/install.json
if errorlevel 1 exit 1
:: python -m pip install . --no-deps -vv

endlocal