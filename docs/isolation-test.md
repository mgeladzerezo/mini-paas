# Proving tenant isolation on a real cluster

The unit tests assert that the right objects are *rendered* for every tenant. They cannot
show that the cluster *enforces* them: that depends on the CNI, the admission
configuration and the EKS add-on settings. This is the procedure for checking it on a
running cluster.

It has not been run as part of building this repository (no cluster was available), so
this document contains the steps and the expected outcome of each, and no recorded
results. Fill in the table at the end when you run it.

## Prerequisites

- A cluster built with `scripts/bootstrap.*`, all Argo CD applications `Synced` and `Healthy`.
- `kubectl` with cluster-admin access.
- Two workspaces created through the UI, called `tenant-a` and `tenant-b` below
  (namespaces `tenant-tenant-a` and `tenant-tenant-b` if those are the workspace ids),
  each with one deployed, healthy app called `web`. Any small HTTP image works.

```sh
A=tenant-tenant-a
B=tenant-tenant-b
```

Confirm the network policy engine is actually running. Without it every network test
below would "fail open" and the policies would be decoration:

```sh
kubectl -n kube-system get ds aws-node -o jsonpath='{.spec.template.spec.containers[*].name}'
# expected: the list includes aws-network-policy-agent (aws-eks-nodeagent on older add-on versions)
kubectl get policyendpoints -A
# expected: entries for both tenant namespaces
```

## 1. The isolation objects exist

```sh
for ns in $A $B; do
  kubectl get ns $ns --show-labels
  kubectl -n $ns get resourcequota,limitrange,networkpolicy,serviceaccount
done
```

Expected in each namespace: label `pod-security.kubernetes.io/enforce=restricted`;
`tenant-quota`; `tenant-limits`; NetworkPolicies `default-deny`,
`allow-ingress-controller`, `allow-dns`; ServiceAccount `workload`.

## 2. Tenant A cannot reach tenant B over the network

The app containers may not have a shell or curl, and `kubectl run` with a debugging
image has to satisfy the restricted profile. Use this pod spec, which does:

```sh
probe() {  # probe <namespace> <name>
  kubectl -n "$1" run "$2" --image=curlimages/curl:8.10.1 --restart=Never \
    --overrides='{"spec":{"serviceAccountName":"workload","automountServiceAccountToken":false,
      "securityContext":{"runAsNonRoot":true,"runAsUser":100,"seccompProfile":{"type":"RuntimeDefault"}},
      "containers":[{"name":"probe","image":"curlimages/curl:8.10.1","command":["sleep","3600"],
        "resources":{"requests":{"cpu":"50m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},
        "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
  kubectl -n "$1" wait --for=condition=Ready pod/"$2" --timeout=60s
}
probe $A probe-a
```

Collect tenant B's addresses:

```sh
B_SVC=$(kubectl -n $B get svc web -o jsonpath='{.spec.clusterIP}')
B_POD=$(kubectl -n $B get pod -l app.kubernetes.io/name=web -o jsonpath='{.items[0].status.podIP}')
B_PORT=$(kubectl -n $B get pod -l app.kubernetes.io/name=web -o jsonpath='{.items[0].spec.containers[0].ports[0].containerPort}')
```

| # | Command (run as `kubectl -n $A exec probe-a -- ...`) | Expected |
|---|---|---|
| 2.1 | `curl -sS -m 5 http://web.$B.svc.cluster.local/` | times out (exit code 28) |
| 2.2 | `curl -sS -m 5 http://$B_SVC/` | times out |
| 2.3 | `curl -sS -m 5 http://$B_POD:$B_PORT/` | times out |
| 2.4 | `nslookup web.$B.svc.cluster.local` | resolves (DNS is allowed; only the connection is blocked) |
| 2.5 | `curl -sS -m 5 https://example.com/` | times out (no internet egress by default) |
| 2.6 | `curl -sS -m 5 http://169.254.169.254/latest/meta-data/` | times out (no node credentials via instance metadata) |
| 2.7 | `curl -sS -m 5 -k https://kubernetes.default.svc/version` | times out |

A timeout, not "connection refused", is the signature of a NetworkPolicy drop. A fast
refusal means the packet arrived and something else rejected it; investigate rather
than counting it as a pass.

Pods inside the same tenant are isolated from each other too (`default-deny` has no
same-namespace exception). Check it so nobody later assumes otherwise:

```sh
A_POD=$(kubectl -n $A get pod -l app.kubernetes.io/name=web -o jsonpath='{.items[0].status.podIP}')
A_PORT=$(kubectl -n $A get pod -l app.kubernetes.io/name=web -o jsonpath='{.items[0].spec.containers[0].ports[0].containerPort}')
kubectl -n $A exec probe-a -- curl -sS -m 5 http://$A_POD:$A_PORT/   # expected: times out
```

## 3. The control experiment: traffic that should work, does

A cluster with broken networking would pass every test in section 2. These must succeed:

```sh
# Through the ingress controller, from outside the cluster:
curl -sS -o /dev/null -w '%{http_code}\n' https://web.tenant-b.apps.<your-zone>/
# expected: the app's normal status code

# From the ingress controller's namespace directly to the pod:
kubectl -n ingress-nginx exec deploy/ingress-nginx-controller -- \
  curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://$B_POD:$B_PORT/
# expected: the app's normal status code
```

Then prove it is the policy doing the blocking. Temporarily remove B's default-deny
and repeat 2.3:

```sh
kubectl -n $B delete networkpolicy default-deny allow-ingress-controller
kubectl -n $A exec probe-a -- curl -sS -m 5 http://$B_POD:$B_PORT/
# expected: STILL times out, because tenant A's own egress policy blocks it.

kubectl -n $A delete networkpolicy default-deny allow-dns
kubectl -n $A exec probe-a -- curl -sS -m 5 http://$B_POD:$B_PORT/
# expected: now succeeds. Both sides were independently blocking it.
```

Restore the policies by redeploying either app from the UI (the deployer re-applies all
tenant objects on every deploy), and confirm with `kubectl get networkpolicy` in both
namespaces. Repeat 2.3 and expect a timeout again.

## 4. Tenant A's workload identity has no API access

```sh
kubectl auth can-i --list --as=system:serviceaccount:$A:workload -n $B
kubectl auth can-i get secrets --as=system:serviceaccount:$A:workload -n $B   # expected: no
kubectl auth can-i get secrets --as=system:serviceaccount:$A:workload -n $A   # expected: no
kubectl auth can-i create pods --as=system:serviceaccount:$A:workload -n $A   # expected: no

kubectl -n $A exec probe-a -- ls /var/run/secrets/kubernetes.io/serviceaccount
# expected: "No such file or directory" - no token is mounted at all
```

`--list` should show only the entries every authenticated identity gets
(`selfsubjectaccessreviews`, `selfsubjectrulesreviews`, non-resource discovery URLs).

## 5. Pod Security admission rejects privileged workloads

Each of these should be rejected by the API server with a `violates PodSecurity
"restricted:latest"` message:

```sh
kubectl -n $A run root-pod --image=busybox --restart=Never -- sleep 60
kubectl -n $A run priv-pod --image=busybox --restart=Never --privileged -- sleep 60
kubectl -n $A run host-pod --image=busybox --restart=Never \
  --overrides='{"spec":{"hostNetwork":true,"containers":[{"name":"c","image":"busybox"}]}}'
```

## 6. Quotas and limits are enforced

```sh
kubectl -n $A describe resourcequota tenant-quota      # "Used" reflects the running app

# A LoadBalancer or NodePort Service would bypass the ingress controller:
kubectl -n $A create service loadbalancer bypass --tcp=80:8080
# expected: forbidden: exceeded quota ... services.loadbalancers

# A container asking for more than the LimitRange maximum:
kubectl -n $A run big --image=busybox --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"c","image":"busybox","resources":{"limits":{"memory":"64Gi"}}}]}}'
# expected: forbidden: maximum memory usage per Container is 2Gi
```

## 7. Secrets do not cross tenants

Set a distinctive env var on tenant B's app (for example `CANARY=only-tenant-b`) and deploy.

```sh
kubectl get secrets -A -o json | grep -c "$(printf 'only-tenant-b' | base64)"
# expected: 1 (only in tenant B's namespace)
kubectl -n $A get secrets -o json | grep -c "$(printf 'only-tenant-b' | base64)"
# expected: 0
kubectl -n $A exec deploy/web -- env | grep CANARY
# expected: no output
```

## 8. Build isolation

While a build is running (`kubectl -n paas-builds get pods -w`):

```sh
kubectl -n paas-builds get pod -o wide            # expected: scheduled on a node labelled paas.dev/pool=builds
kubectl get pods -A -o wide --field-selector spec.nodeName=<that node>
# expected: besides daemonsets, only build pods
```

And from tenant A, the builds namespace is unreachable like any other (same as 2.3 with a
build pod's IP).

## Clean up

```sh
kubectl -n $A delete pod probe-a --ignore-not-found
```

## Results

| Section | Date | Cluster / add-on versions | Pass / fail | Notes |
|---|---|---|---|---|
| 1 Objects exist | | | | |
| 2 A cannot reach B | | | | |
| 3 Control experiment | | | | |
| 4 No API access | | | | |
| 5 Pod Security | | | | |
| 6 Quotas and limits | | | | |
| 7 Secrets | | | | |
| 8 Build isolation | | | | |
