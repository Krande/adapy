import pathlib

import uvicorn
from fastapi import FastAPI, HTTPException
from starlette.responses import Response

app = FastAPI()

_build_dir = pathlib.Path("docs/_build/html").resolve().absolute()


# serve a html directory _build/html with index.html as the default page
@app.get("/{file_path:path}")
async def serve_static_files(file_path: str):
    if file_path == "":
        file_path = "index.html"

    # Resolve the full path and validate that it is inside the _build_dir
    try:
        resolved_path = ( _build_dir / file_path ).resolve(strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path.")
    # Check that the resolved path starts with the build dir (prevents path traversal)
    if not str(resolved_path).startswith(str(_build_dir)):
        raise HTTPException(status_code=403, detail="Access is forbidden.")

    # Check if the file exists locally
    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found locally.")

    # Determine the content type based on the file extension
    content_type = "application/octet-stream"
    if file_path.endswith(".html"):
        content_type = "text/html"
    elif file_path.endswith(".css"):
        content_type = "text/css"
    elif file_path.endswith(".js"):
        content_type = "application/javascript"
    elif file_path.endswith(".png"):
        content_type = "image/png"
    elif file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif file_path.endswith(".svg"):
        content_type = "image/svg+xml"

    # Serve the file content from the local directory
    with open(resolved_path, "rb") as f:
        return Response(content=f.read(), media_type=content_type)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
