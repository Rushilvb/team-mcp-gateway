{{/* Common labels on every resource. */}}
{{- define "platform.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* Selector labels. Usage: include "platform.selectorLabels" (dict "ctx" $ "name" "woo-mcp") */}}
{{- define "platform.selectorLabels" -}}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{/*
"Use explicit value, else keep the existing one, else generate." Same pattern
as the store chart: `lookup` reads the live Secret so `helm upgrade` never
rotates passwords/tokens by accident (empty during `helm template`).
Usage: include "platform.secretValue" (dict "ctx" $ "secret" "postgres-notes" "key" "password" "override" "" "generate" (randAlphaNum 32))
*/}}
{{- define "platform.secretValue" -}}
{{- if .override -}}
{{- .override -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace .secret -}}
{{- $data := dict -}}
{{- if $existing }}{{- $data = $existing.data | default dict -}}{{- end -}}
{{- if hasKey $data .key -}}
{{- index $data .key | b64dec -}}
{{- else -}}
{{- .generate -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Host headers an MCP server accepts (the SDK's DNS-rebinding protection): the
Service's in-cluster names, plus localhost for kubectl port-forward.
Usage: include "platform.mcpAllowedHosts" (dict "ctx" $ "name" "notes-mcp")
*/}}
{{- define "platform.mcpAllowedHosts" -}}
{{- $ns := .ctx.Release.Namespace -}}
{{ .name }}:*,{{ .name }}.{{ $ns }}:*,{{ .name }}.{{ $ns }}.svc:*,{{ .name }}.{{ $ns }}.svc.cluster.local:*,localhost:*,127.0.0.1:*
{{- end }}

{{- define "platform.storageClass" -}}
{{- if .Values.storageClass -}}
storageClassName: {{ .Values.storageClass }}
{{- end -}}
{{- end }}

{{/* Database connection env for the gateway (libpq names; the app builds its URL from them). */}}
{{- define "gateway.dbEnv" -}}
- name: PGHOST
  value: postgres
- name: PGDATABASE
  value: gateway
- name: PGUSER
  value: gateway
- name: PGPASSWORD
  valueFrom: { secretKeyRef: { name: postgres-gateway, key: password } }
{{- end }}

{{/* Pull secrets for our own images (only needed for a PRIVATE registry, e.g. private GHCR packages). */}}
{{- define "platform.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
