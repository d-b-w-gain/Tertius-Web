{{- define "tertius.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "tertius.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "tertius.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "tertius.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "tertius.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tertius.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "tertius.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tertius.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "tertius.apiName" -}}
{{- printf "%s-api" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.uiName" -}}
{{- printf "%s-ui" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.otelCollectorName" -}}
{{- printf "%s-otel-collector" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.otelCollectorConfigName" -}}
{{- printf "%s-config" (include "tertius.otelCollectorName" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.metricsBackendName" -}}
{{- printf "%s-victoriametrics" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.tracesBackendName" -}}
{{- printf "%s-victoriatraces" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.configName" -}}
{{- printf "%s-config" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.secretName" -}}
{{- default (printf "%s-app" (include "tertius.fullname" .)) .Values.app.secretName | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.appDbName" -}}
{{- default (printf "%s-postgres" (include "tertius.fullname" .)) .Values.postgres.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.keycloakDbName" -}}
{{- default (printf "%s-keycloak-postgres" (include "tertius.fullname" .)) .Values.keycloak.database.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.keycloakName" -}}
{{- default (printf "%s-keycloak" (include "tertius.fullname" .)) .Values.keycloak.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tertius.piAgentAuthClaimName" -}}
{{- if .Values.piAgent.auth.existingClaim -}}
{{- .Values.piAgent.auth.existingClaim -}}
{{- else -}}
{{- printf "%s-pi-agent-auth" (include "tertius.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
