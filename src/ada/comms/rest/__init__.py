"""REST API for the hosted ada-py viewer.

Serves the same flatbuffer Message envelope used by the WebSocket
transport over HTTP, plus a small set of viewer-only endpoints
(/api/config, /api/blobs/{key}). Storage is backed by obstore so the
same code runs against S3-compatible stores (Garage, MinIO) and a
local filesystem for dev/testing.
"""
