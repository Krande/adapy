# adapy-viewer Helm chart

Deploys the hosted ada-py viewer: a single-pod web app that loads
3D model files (FEA, CAD, GLB) from S3-compatible storage and renders
them in the browser.

## Quick start (existing S3 / Garage / MinIO)

```bash
helm install viewer ./deploy/helm/adapy-viewer \
  --set storage.s3.endpoint=http://garage:3900 \
  --set storage.s3.bucket=models \
  --set storage.s3.region=garage \
  --set storage.s3.accessKeyId=YOUR_KEY \
  --set storage.s3.secretAccessKey=YOUR_SECRET
```

Or, with credentials in an existing Secret:

```bash
helm install viewer ./deploy/helm/adapy-viewer \
  --set storage.s3.endpoint=http://garage:3900 \
  --set storage.s3.bucket=models \
  --set storage.s3.existingSecret=my-s3-creds
```

The chart expects the existing Secret to have keys
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` — override
`storage.s3.existingSecretKeyId` / `existingSecretSecretKey` if yours
uses different names.

## Quick start (self-contained, with bundled Garage)

```bash
helm install viewer ./deploy/helm/adapy-viewer \
  --set garage.enabled=true \
  --set storage.s3.bucket=models \
  --set storage.s3.accessKeyId=local-dev \
  --set storage.s3.secretAccessKey=local-dev-secret
```

This deploys Garage in the same release. The viewer's `S3_ENDPOINT`
is wired automatically to the in-cluster Garage service. You still
need to create a bucket and upload GLBs into it after the pod is up
(see Garage's docs).

> **Note.** The bundled Garage is intended for dev/demo. It runs as a
> single replica with a placeholder admin token. Replace
> `garage.adminToken` and `garage.rpcSecret` for anything production-y.

## Local-FS mode (no S3)

For testing without object storage, point the API at a directory
mounted into the pod:

```bash
helm install viewer ./deploy/helm/adapy-viewer \
  --set storage.kind=local \
  --set storage.local.existingClaim=my-pvc
```

## Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  host: viewer.example.com
  tls:
    - secretName: viewer-tls
      hosts: [viewer.example.com]
```

## Image

Build from the repo root:

```bash
docker build -f deploy/Dockerfile.viewer -t ghcr.io/krande/adapy-viewer:dev .
```

Then point the chart at it:

```bash
helm install viewer ./deploy/helm/adapy-viewer \
  --set image.repository=ghcr.io/krande/adapy-viewer \
  --set image.tag=dev
```
