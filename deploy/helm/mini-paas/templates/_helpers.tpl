{{- define "mini-paas.labels" -}}
app.kubernetes.io/part-of: mini-paas
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "mini-paas.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/name: {{ .component }}
{{- end }}

{{- define "mini-paas.backendImage" -}}
{{ .Values.image.registry }}/mini-paas/api:{{ .Values.image.tag }}
{{- end }}

{{- define "mini-paas.envSecret" -}}
{{ .Release.Name }}-env
{{- end }}

{{/* Settings shared by the API and the worker; both read the same PAAS_* variables. */}}
{{- define "mini-paas.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ .Release.Name }}-config
  - secretRef:
      name: {{ include "mini-paas.envSecret" . }}
{{- end }}

{{- define "mini-paas.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "mini-paas.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}
