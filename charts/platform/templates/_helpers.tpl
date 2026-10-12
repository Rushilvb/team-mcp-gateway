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
