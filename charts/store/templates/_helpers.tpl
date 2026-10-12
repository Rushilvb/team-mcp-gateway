{{/* Common labels on every resource. */}}
{{- define "store.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: store
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* Selector labels. Usage: include "store.selectorLabels" (dict "ctx" $ "name" "wordpress") */}}
{{- define "store.selectorLabels" -}}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{- define "store.siteUrl" -}}
{{ ternary "https" "http" .Values.ingress.tls.enabled }}://{{ .Values.ingress.host }}
{{- end }}

{{- define "store.storageClass" -}}
{{- if .Values.storageClass -}}
storageClassName: {{ .Values.storageClass }}
{{- end -}}
{{- end }}

{{/*
"Use explicit value, else keep the existing one, else generate."
Without the middle step every `helm upgrade` would generate new random
passwords and lock WordPress out of its own database.
`lookup` asks the live cluster for the Secret; it returns an empty map on
first install (and during `helm template`, which has no cluster).
Usage: include "store.secretValue" (dict "ctx" $ "secret" "store-db" "key" "password" "override" .Values.x "generate" (randAlphaNum 32))
*/}}
{{- define "store.secretValue" -}}
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

{{/* 40 lowercase hex chars, the same shape as WooCommerce's wc_rand_hash(). */}}
{{- define "store.randHex40" -}}
{{- randAlphaNum 64 | sha1sum -}}
{{- end }}
