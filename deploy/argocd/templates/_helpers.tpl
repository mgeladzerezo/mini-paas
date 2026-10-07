{{/*
Common Application boilerplate. Arguments: name, namespace, wave.
Add-ons sync in wave 0 so their CRDs exist before the platform (wave 1) creates
ClusterIssuer and ExternalSecret objects.
*/}}
{{- define "apps.metadata" -}}
name: {{ .name }}
namespace: argocd
annotations:
  argocd.argoproj.io/sync-wave: {{ .wave | quote }}
finalizers:
  - resources-finalizer.argocd.argoproj.io
{{- end }}

{{- define "apps.destinationAndSync" -}}
destination:
  server: https://kubernetes.default.svc
  namespace: {{ .namespace }}
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
    - CreateNamespace=true
    - ServerSideApply=true
{{- end }}
