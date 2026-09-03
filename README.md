# ocp-argocd-vechicle-booking-poc

Self-contained ArgoCD/GitOps deployment for the **Car Rental Booking Platform** POC, targeting the `lab.ocp.local` OpenShift cluster (OCP 4.20.35). Twelve lightweight, open-source microservices — no proprietary dependencies.

Start here:
- [`car-rental-poc-architecture.md`](car-rental-poc-architecture.md) — HLD/LLD, reviewed against live cluster state
- [`car-rental-poc-execution-guide.md`](car-rental-poc-execution-guide.md) — step-by-step deployment runbook

## Layout

```
apps/
  app-of-apps/            # AppProject "car-rental" + root Application (bootstrap, applied manually once)
  car-rental/
    base/                  # Kustomize base: 8 business svcs + postgresql + redis + ollama + gateway + web-ui
    overlays/{dev,prod}/   # namespace + replica + image-tag overrides per environment
platform/
  multi-tenancy/manual/    # ResourceQuota/LimitRange — applied by hand, ArgoCD can't self-grant quota (the one deliberate exception, see architecture doc)
services/
  generic-svc/             # shared Flask scaffold image source for the 8 business services
.github/
  CODEOWNERS                # required reviewer for every PR
  workflows/
    car-rental-ci.yaml        # build/push on services/generic-svc/** changes, opens a PR bumping dev's tag
    car-rental-promote.yaml   # manual workflow_dispatch, opens a PR bumping prod's tag
    validate-car-rental.yaml  # yaml-lint + kustomize build + kind dry-run — required status check on every PR/push
```

Everything under `apps/` — namespaces, NetworkPolicies, RBAC, all 12 workloads, PodDisruptionBudgets — is ArgoCD-managed. `platform/multi-tenancy/manual/` is the one deliberate exception (security control, not an oversight — see architecture doc's risk table).

**No workflow pushes to `main` directly** — both CI bots only open PRs; branch protection (1 required approval, required status checks, no direct pushes, enforced on admins too) gates every merge, human or bot. **✅ Live** — verified 2026-09-03 by a rejected direct push. See execution guide Phase -1.

## Bootstrap (one-time)

```bash
oc apply -f apps/app-of-apps/project.yaml
oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops
```

Full sequence, gates, and verification commands are in the execution guide.

## Status

Design + all manifests generated and validated (`oc kustomize` builds clean for every overlay) 2026-09-03. HA pod placement (topology spread + PDBs) and PR-gated CI added the same day. The unused `redis-platform` namespace/Application/AppProject and a stray `redis-bench-loop` pod were deleted from the cluster to free capacity. Branch protection is live and verified. Nothing from `apps/` has been applied to the cluster yet — awaiting review before Phase 00 bootstrap.
