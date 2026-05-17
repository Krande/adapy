{{/*
Expand the name of the chart.
*/}}
{{- define "adapy-viewer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "adapy-viewer.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "adapy-viewer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "adapy-viewer.labels" -}}
helm.sh/chart: {{ include "adapy-viewer.chart" . }}
{{ include "adapy-viewer.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "adapy-viewer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "adapy-viewer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "adapy-viewer.garageFullname" -}}
{{- printf "%s-garage" (include "adapy-viewer.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
S3 endpoint used by the API. If garage.enabled, point at the in-cluster
Garage service. Otherwise use the explicit endpoint from values.
*/}}
{{- define "adapy-viewer.s3Endpoint" -}}
{{- if .Values.garage.enabled -}}
{{- printf "http://%s:%d" (include "adapy-viewer.garageFullname" .) (int .Values.garage.service.s3Port) -}}
{{- else -}}
{{- .Values.storage.s3.endpoint -}}
{{- end -}}
{{- end -}}

{{- define "adapy-viewer.s3SecretName" -}}
{{- if .Values.storage.s3.existingSecret -}}
{{- .Values.storage.s3.existingSecret -}}
{{- else -}}
{{- printf "%s-s3" (include "adapy-viewer.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "adapy-viewer.workerFullname" -}}
{{- printf "%s-worker" (include "adapy-viewer.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Worker Deployment body. Called from worker.yaml (base pool) and
worker-extras.yaml (extraWorkers entries). Single source of truth for
the env block, volumes, security context — both pools stay in lockstep
when the surface evolves.

Args via a dict argument:
  ctx        — root context (.)
  w          — the pool's values block (.Values.worker or one extraWorkers entry)
  name       — Deployment name
  component  — component label (e.g. "worker" or "worker-<key>")
*/}}
{{- define "adapy-viewer.workerDeployment" -}}
{{- $ctx := .ctx -}}
{{- $w := .w -}}
{{- $name := .name -}}
{{- $component := .component -}}
{{- $natsUrl := include "adapy-viewer.natsUrl" $ctx -}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  labels:
    {{- include "adapy-viewer.labels" $ctx | nindent 4 }}
    app.kubernetes.io/component: {{ $component }}
spec:
  replicas: {{ $w.replicaCount | default 1 }}
  selector:
    matchLabels:
      {{- include "adapy-viewer.selectorLabels" $ctx | nindent 6 }}
      app.kubernetes.io/component: {{ $component }}
  template:
    metadata:
      labels:
        {{- include "adapy-viewer.selectorLabels" $ctx | nindent 8 }}
        app.kubernetes.io/component: {{ $component }}
      {{- with $w.podAnnotations }}
      annotations: {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- with $ctx.Values.imagePullSecrets }}
      imagePullSecrets: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $ctx.Values.hostAliases }}
      hostAliases: {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: worker
          image: "{{ $w.image.repository }}:{{ $w.image.tag | default $ctx.Chart.AppVersion }}"
          imagePullPolicy: {{ $w.image.pullPolicy }}
          env:
            - name: ADA_VIEWER_STORAGE_KIND
              value: {{ $ctx.Values.storage.kind | quote }}
            {{- if eq $ctx.Values.storage.kind "s3" }}
            - name: ADA_VIEWER_S3_BUCKET
              value: {{ $ctx.Values.storage.s3.bucket | quote }}
            - name: ADA_VIEWER_S3_ENDPOINT
              value: {{ include "adapy-viewer.s3Endpoint" $ctx | quote }}
            - name: ADA_VIEWER_S3_REGION
              value: {{ $ctx.Values.storage.s3.region | quote }}
            - name: ADA_VIEWER_S3_PREFIX
              value: {{ $ctx.Values.storage.s3.prefix | quote }}
            - name: ADA_VIEWER_S3_VIRTUAL_HOSTED_STYLE
              value: {{ $ctx.Values.storage.s3.virtualHostedStyle | quote }}
            - name: ADA_VIEWER_S3_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: {{ include "adapy-viewer.s3SecretName" $ctx }}
                  key: {{ $ctx.Values.storage.s3.existingSecretKeyId | default "AWS_ACCESS_KEY_ID" }}
            - name: ADA_VIEWER_S3_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "adapy-viewer.s3SecretName" $ctx }}
                  key: {{ $ctx.Values.storage.s3.existingSecretSecretKey | default "AWS_SECRET_ACCESS_KEY" }}
            {{- else if eq $ctx.Values.storage.kind "local" }}
            - name: ADA_VIEWER_LOCAL_PATH
              value: /data
            {{- end }}
            - name: ADA_VIEWER_NATS_URL
              value: {{ $natsUrl | quote }}
            - name: ADA_WORKER_CAPABILITIES
              value: {{ (default (list "base") $w.capabilities) | join "," | quote }}
            {{- with $w.extAllow }}
            # Optional per-pod source-suffix allowlist. When set,
            # adapy's worker only picks up jobs whose source key has
            # one of these suffixes (see worker.py:683). Useful for
            # capability pools (e.g. abaqus) that FROM the base image
            # and inherit its full stream-reader registry but should
            # only handle their own formats — without this gate they
            # race the base pool for shared extensions like ``.rmed``
            # and a stale capability-pod can fail jobs the base pod
            # would have handled fine. Unset → handle everything in
            # the registry (right default for the base pool).
            - name: ADA_WORKER_EXT_ALLOW
              value: {{ . | join "," | quote }}
            {{- end }}
            {{- include "adapy-viewer.databaseEnv" $ctx | nindent 12 }}
          {{- if eq $ctx.Values.storage.kind "local" }}
          volumeMounts:
            - name: data
              mountPath: /data
          {{- end }}
          resources: {{- toYaml $w.resources | nindent 12 }}
          {{- with $w.securityContext }}
          securityContext: {{- toYaml . | nindent 12 }}
          {{- end }}
      {{- if eq $ctx.Values.storage.kind "local" }}
      volumes:
        - name: data
          {{- if $ctx.Values.storage.local.existingClaim }}
          persistentVolumeClaim:
            claimName: {{ $ctx.Values.storage.local.existingClaim }}
          {{- else if $ctx.Values.storage.local.hostPath }}
          hostPath:
            path: {{ $ctx.Values.storage.local.hostPath | quote }}
            type: DirectoryOrCreate
          {{- else }}
          emptyDir: {}
          {{- end }}
      {{- end }}
{{- end -}}

{{- define "adapy-viewer.natsFullname" -}}
{{- printf "%s-nats" (include "adapy-viewer.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
NATS URL the API and worker connect to. If nats.enabled, point at the
in-cluster NATS service. Otherwise use the explicit URL from values
(empty disables conversion entirely on the API side).
*/}}
{{- define "adapy-viewer.natsUrl" -}}
{{- if .Values.nats.enabled -}}
{{- printf "nats://%s:%d" (include "adapy-viewer.natsFullname" .) (int .Values.nats.service.clientPort) -}}
{{- else -}}
{{- .Values.nats.url -}}
{{- end -}}
{{- end -}}

{{/*
Auth env block. Rendered into the api Deployment when auth.enabled is
true. Worker pods don't need auth (no inbound HTTP). The block emits
nothing when disabled, leaving server defaults — which keep the API
open and untouched from phase-1-and-earlier behavior.
*/}}
{{- define "adapy-viewer.authEnv" -}}
{{- if .Values.auth.enabled }}
- name: ADA_VIEWER_AUTH_ENABLED
  value: "true"
- name: ADA_VIEWER_AUTH_ISSUER
  value: {{ .Values.auth.issuer | quote }}
- name: ADA_VIEWER_AUTH_CLIENT_ID
  value: {{ .Values.auth.clientId | quote }}
{{- if .Values.auth.audience }}
- name: ADA_VIEWER_AUTH_AUDIENCE
  value: {{ .Values.auth.audience | quote }}
{{- end }}
{{- if .Values.auth.adminGroup }}
- name: ADA_VIEWER_AUTH_ADMIN_GROUP
  value: {{ .Values.auth.adminGroup | quote }}
{{- end }}
{{- if and .Values.auth.cliTokenSecret .Values.auth.cliTokenSecret.existingSecret }}
- name: ADA_VIEWER_CLI_TOKEN_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.cliTokenSecret.existingSecret }}
      key: {{ .Values.auth.cliTokenSecret.existingSecretKey | default "cli_token_secret" }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "adapy-viewer.postgresFullname" -}}
{{- printf "%s-pg" (include "adapy-viewer.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "adapy-viewer.postgresSecretName" -}}
{{- if .Values.postgres.existingSecret -}}
{{- .Values.postgres.existingSecret -}}
{{- else -}}
{{- printf "%s-pg" (include "adapy-viewer.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "adapy-viewer.postgresPasswordKey" -}}
{{- if .Values.postgres.existingSecret -}}
{{- .Values.postgres.existingSecretPasswordKey | default "password" -}}
{{- else -}}
password
{{- end -}}
{{- end -}}

{{/*
DATABASE_URL env block. Rendered into both the api and worker
deployments. Three modes, in priority order:

  1. postgres.enabled — bundled in-cluster Postgres. POSTGRES_PASSWORD
     is pulled from the chart-managed (or referenced) Secret, then
     DATABASE_URL is built using kubelet's $(VAR) substitution — that
     way the password never needs to materialize in the rendered
     manifest, but the DSN still ends up usable.
  2. database.existingSecret — external Postgres, DSN read from the
     named Secret. Preferred for production / gitops.
  3. database.url — external Postgres, inline DSN. Acceptable for dev.

When none apply, no DATABASE_URL env is emitted and the API runs in
shared-only mode.
*/}}
{{- define "adapy-viewer.databaseEnv" -}}
{{- if .Values.postgres.enabled }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "adapy-viewer.postgresSecretName" . }}
      key: {{ include "adapy-viewer.postgresPasswordKey" . }}
- name: DATABASE_URL
  value: {{ printf "postgres://%s:$(POSTGRES_PASSWORD)@%s:%d/%s" .Values.postgres.username (include "adapy-viewer.postgresFullname" .) (int .Values.postgres.service.port) .Values.postgres.database | quote }}
{{- else if .Values.database.existingSecret }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret }}
      key: {{ .Values.database.existingSecretKey | default "DATABASE_URL" }}
{{- else if .Values.database.url }}
- name: DATABASE_URL
  value: {{ .Values.database.url | quote }}
{{- end }}
{{- end -}}
