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
  multi-tenancy/manual/    # ResourceQuota/LimitRange — applied by hand, ArgoCD can't self-grant quota
services/
  generic-svc/             # shared Flask scaffold image source for the 8 business services
.github/workflows/
  car-rental-ci.yaml        # build/push on services/generic-svc/** changes, auto-commits tag to dev
  car-rental-promote.yaml   # manual workflow_dispatch, promotes a validated tag to prod
  validate-car-rental.yaml  # yaml-lint + kustomize build + kind dry-run on every PR/push
```

## Bootstrap (one-time)

```bash
oc apply -f apps/app-of-apps/project.yaml
oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops
```

Full sequence, gates, and verification commands are in the execution guide.

## Status

Design + all manifests generated and validated (`oc kustomize` builds clean for every overlay) 2026-09-03 — not yet applied to the cluster. Awaiting review before Phase 00 bootstrap.
