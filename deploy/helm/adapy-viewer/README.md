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

## DNV-RP-C201 Capacity Result Sidecars

The viewer can visualize DNV-RP-C201 capacity results for SIN-derived FEA
models when a capacity sidecar is available. The normal SIN bake still provides
the geometry and FE result fields; the sidecar adds capacity model membership,
usage factors, check details, and per-element visual fields.

Preferred colocated artefacts:

```text
model.SIN
_derived/model.SIN.fea/fea.manifest.json
_derived/model.SIN.fea/capacity.results.json
```

The manifest may also advertise a sidecar explicitly:

```json
{
  "capacity": {
    "version": 1,
    "results_url": "capacity.results.json",
    "default_run_id": "run-001",
    "field_strategy": "json"
  }
}
```

If the manifest has no `capacity` section, the frontend tries these fallback
locations:

- `_derived/<source>.fea/capacity.results.json`
- `<source-stem>.c201.json`
- `<source-stem>.capacity.json`
- `capacity.results.json` next to the source

Generate the sidecar from the DNV-RP-C201 package:

```bash
run-codecheck --sin model.SIN --group Mini_area_dbl_btm --export-viewer out/
```

When the sidecar is found, the Capacity panel appears in the simulation
controls. It supports Definition and Results modes, result-case and metric
selection, failed-only filtering, usage-factor coloring, capacity boundary
outlines, and element-pick to capacity-model selection.

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
