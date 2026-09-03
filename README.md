# ocp-argocd-vechicle-booking-poc

ArgoCD/GitOps deployment repo for the **car rental booking** POC (resources named `vehicle-booking` to match the repo), targeting the
`lab.ocp.local` OpenShift cluster. Follows the same App-of-Apps + Kustomize
pattern as [`ocp-gitops-poc`](https://github.com/esarath/ocp-gitops-poc).

## Layout

```
apps/
  app-of-apps/          # ArgoCD Application + AppProject that bootstraps everything else
  vehicle-booking/
    base/                # Kustomize base manifests (Deployment, Service, ...)
    overlays/
      stage/
      prod/
docs/                    # design notes, runbooks
```

## Bootstrap

```bash
oc apply -f apps/app-of-apps/project.yaml
oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops
```

This registers an ArgoCD `AppProject` (`vehicle-booking`) and a root
`Application` that syncs everything under `apps/app-of-apps/` — including the
`stage` and `prod` overlays for the `vehicle-booking` workload.

## Status

Scaffold only — application manifests under `apps/vehicle-booking/base` are
placeholders to be replaced with the real car rental booking service.
