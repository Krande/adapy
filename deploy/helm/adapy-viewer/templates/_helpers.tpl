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
