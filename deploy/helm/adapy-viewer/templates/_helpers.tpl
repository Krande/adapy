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
