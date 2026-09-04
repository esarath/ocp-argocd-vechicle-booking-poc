# Car Rental Booking Platform — Architecture Design Record

**Architecture Design Record · POC**
Enterprise-style HLD, LLD and deployment record for a lightweight, open-source, GitOps-managed microservices POC running on the existing OpenShift lab cluster — a fully self-contained tenant, not layered onto another app's GitOps repo.

| | |
|---|---|
| **Cluster** | lab.ocp.local · OCP 4.20.35 |
| **GitOps** | esarath/ocp-argocd-vechicle-booking-poc (this repo — self-contained: its own `app-of-apps`, `AppProject`, CI) |
| **Delivery** | ArgoCD + GitHub Actions |
| **Status** | Design reviewed against live cluster 2026-09-03 · Dev pending first sync · Prod pending sign-off |

---

## 00 · Executive summary

The platform books a car (TATA/Mahindra/Honda/Force/Maruti, each with its own per-km rate), a route between five Indian cities, and a set of travelers, then confirms a fare and a mock payment. It is built as twelve small, independently deployable units — eight business microservices plus PostgreSQL, Redis, an Ollama-based AI runtime, an API gateway and a web UI — all free/open-source, sized deliberately small to fit the lab cluster's real spare capacity.

- **12** Deployments + 1 StatefulSet: 8 business services + 4 platform components
- **2** dedicated tenant namespaces — `car-rental-dev`, `car-rental-prod` — this repo's own, not shared with any other tenant on the cluster
- **~6.5Gi** free worker memory / **~3.7 vCPU** free worker CPU on the lab cluster, measured directly against node `status.allocatable` at review time (2026-09-03) — the real ceiling every sizing decision below is checked against, not an estimate

Governance mirrors the pattern already proven on this cluster in `ocp-gitops-poc`'s multi-tenancy work — GitOps-managed namespace/network policy/RBAC via ArgoCD, quota and limits applied manually by a cluster-admin (the ArgoCD controller is deliberately not permitted to grant its own quota) — but this repo owns its **own** `AppProject` (`car-rental`) and root `Application` (`car-rental-app-of-apps`) rather than extending another repo's. That keeps this POC deployable and removable independently of anything else on the cluster.

> **Design decision, recorded 2026-09-03:** an earlier draft of this doc targeted `esarath/ocp-gitops-poc` (extending its live `app-of-apps`/`multi-tenancy` AppProject). Confirmed against the live cluster that no such branch/extension existed yet, and the owner chose to keep this POC fully self-contained in its own repo instead — full isolation, more upfront GitOps wiring (its own bootstrap, AppProject, CI), no shared blast radius with the redis/multi-tenancy/sample-app work already live in `ocp-gitops-poc`.

> **Cluster cleanup, done 2026-09-03:** the `redis-platform` namespace, its ArgoCD `Application` (`redis-platform-appl`, sourced from a separate `esarath/redis-gitops` repo) and its `AppProject` (`redis-project`) were deleted — confirmed unused (the actual Redis workload behind it, `redis-app`/`redis-db`, had already been torn down in an earlier session; only a `kube-state-metrics-redis` monitoring scaffold with no Redis instance to monitor remained). This released a small amount of cluster capacity back to the pool this POC draws from. `redis-bench-loop`, a second unrelated leftover pod in `default`, is also now gone.

---

# High-Level Design

## HLD 01 · System context

Who talks to the platform, and what the platform talks to outside its own namespace.

```
 Customer ───────┐                                        ┌── GitHub (source + Actions CI)
                 ▼                                        │
           ┌─────────────────────────────────┐            │
           │  Car Rental Booking Platform     │◄───────────┘
           │  car-rental-dev / car-rental-prod │────────► ghcr.io (container images)
           │  12 services · OCP namespace     │
           │  fronted by api-gateway + web-ui │────────► Model registry (Ollama model pull, egress)
           │  governed by ArgoCD + tenant quota │
           └─────────────────────────────────┘
                 ▲
 Platform team ──┘  (oc / ArgoCD UI)
```

Customers reach the platform through routes on `api-gateway`/`web-ui`; the platform team operates it via `oc` and the ArgoCD console. Outbound, the platform depends on GitHub Actions CI, its own image registry (`ghcr.io/esarath/car-rental-svc`), and (once) an external model pull for Ollama.

## HLD 02 · Component architecture

Four layers, edge to data:

**Edge**
- `web-ui` — static page, one Route
- `api-gateway` — nginx reverse proxy, one Route

**Business services**
- `catalog-svc`, `location-svc`, `traveler-svc`, `pricing-svc`, `payment-svc` — called, never call out (except pricing-svc's own lookups)
- `booking-svc` — the one orchestrator: calls pricing-svc, payment-svc, traveler-svc

**AI / MCP**
- `chatbot-svc` — talks to Ollama for language, to mcp-server for tool calls
- `mcp-server` — wraps catalog/location/booking as callable tools
- `ollama` — llama3.2:1b, CPU inference

**Data**
- `PostgreSQL` — one instance, 5 schemas
- `Redis` — cache + chat session

Call shape: `web-ui`/`api-gateway` → business services → `booking-svc` fans out to `pricing-svc` (which reads `catalog-svc`/`location-svc`), `payment-svc`, `traveler-svc`. `chatbot-svc` → `mcp-server` → {catalog, location, booking}-svc, and `chatbot-svc` → `ollama` directly. Every data-owning service reads/writes its own Postgres schema; `pricing-svc` and `chatbot-svc` use Redis.

## HLD 03 · Tenancy & environments

Two namespaces, both owned end to end by this repo — no shared tenants, no dependency on another repo's AppProject.

| | openshift-gitops | car-rental-dev | car-rental-prod |
|---|---|---|---|
| Role | This repo's own `car-rental` AppProject + root `Application`; quota/limits applied manually (controller can't self-grant quota) | 1 replica per service, auto-sync + self-heal, auto-promoted by CI | 2 replicas: booking, gateway, ui; auto-sync + self-heal; manual promote workflow only |
| Requests | — | 1.5 CPU / 2Gi | 2 CPU / 3Gi |
| Limits | — | 3 CPU / 4Gi | 4 CPU / 6Gi |
| Pods | — | 20 | 25 |

Both tenant namespaces get identical governance shape (default-deny NetworkPolicy + 5 explicit allows, namespace-scoped `admin` RBAC, restricted Pod Security) — only the quota ceiling and replica counts differ.

## HLD 04 · Request & delivery flow

**Booking request**
web-ui → api-gateway → booking-svc → pricing-svc (rate × distance via catalog-svc + location-svc) → payment-svc → confirmation back to the customer. Traveler records attach via traveler-svc, editable after creation.

**Code-to-production**
Push to `main` (path `services/generic-svc/**`) → GitHub Actions builds/tests/pushes an image to ghcr.io → CI commits the new tag into `apps/car-rental/overlays/dev/kustomization.yaml` → ArgoCD auto-syncs dev → validated → a manual `workflow_dispatch` (`car-rental-promote.yaml`) promotes the tag into `apps/car-rental/overlays/prod/kustomization.yaml` → ArgoCD auto-syncs prod.

---

# Low-Level Design

## LLD 01 · Namespaces & resource quota

| Setting | car-rental-dev | car-rental-prod |
|---|---|---|
| requests.cpu / requests.memory | 1.5 / 2Gi | 2 / 3Gi |
| limits.cpu / limits.memory | 3 / 4Gi | 4 / 6Gi |
| pods | 20 | 25 |
| count/deployments.apps | 15 | 15 |
| persistentvolumeclaims | 3 | 3 |
| Pod Security | `restricted` (enforce / audit / warn) | same |
| Namespace admin | scoped `admin` ClusterRole via RoleBinding — never cluster-admin | same |
| Quota/limit application | manual, cluster-admin, out-of-band — ArgoCD's controller SA cannot create these objects by design | same |

**Per-workload sizing, checked against real node allocatable (2026-09-03)**

| Component | Count | Req. mem | Req. cpu | Lim. mem | Lim. cpu |
|---|---|---|---|---|---|
| catalog / location / traveler / pricing / booking / payment / chatbot / mcp-server | 8 | 64Mi | 50m | 128Mi | 100m |
| api-gateway, web-ui (nginx) | 2 | 32Mi | 25m | 64Mi | 50m |
| PostgreSQL (StatefulSet) | 1 | 256Mi | 100m | 512Mi | 500m |
| Redis | 1 | 64Mi | 50m | 128Mi | 100m |
| Ollama (AI runtime) | 1 | 512Mi | 250m | 1536Mi | 1000m |
| **Total · dev (1× each)** | **13 pods** | **~1.4Gi** | **~0.85** | **~3.3Gi** | **~2.5** |

> ⚠️ **Memory:** stage + prod concurrently is comfortable — real free worker memory measured 2026-09-03 is **~6.5Gi combined** (worker-1 allocatable 8087Mi / used 4771Mi, worker-2 allocatable 8087Mi / used 4735Mi), better than an earlier conservative ~5Gi estimate. Combined dev+prod requests (2Gi+3Gi=5Gi) fit with headroom to spare; combined limits (4Gi+6Gi=10Gi) overcommit against free memory, which is normal/expected for limits.
>
> ⚠️ **CPU is the tighter constraint, not memory** — real free worker CPU measured the same day is **~3.7 vCPU combined** (2×2500m allocatable, worker-1 at 12% used / worker-2 at 38% used). Dev+prod combined CPU *requests* alone are 3.5 vCPU — within budget, but with almost no slack once ArgoCD, existing tenants, and cluster infra are accounted for. Validate dev with `oc adm top pods` before turning on prod load, same as the memory check, and don't assume memory being fine also means CPU is fine.

## LLD 01a · Pod placement & high availability

The cluster has exactly 2 schedulable workers (`worker-1`, `worker-2`) — "HA" here means spreading replicas across those 2 real workers and surviving a single voluntary disruption, not a textbook 3+-zone design the cluster doesn't have the nodes for.

**Where 2 replicas actually matter (prod only):** `booking-svc`, `api-gateway`, `web-ui` — the request path a customer or a node drain would actually hit. Everything else (the other 7 business services, `postgresql`, `redis`, `ollama`) stays single-replica by design: they're either stateful singletons (Postgres, Ollama's model PVC) or not yet carrying real traffic (the other business services).

- **`topologySpreadConstraints`** on all three (`maxSkew: 1`, `topologyKey: kubernetes.io/hostname`, `whenUnsatisfiable: ScheduleAnyway`) — defined once in `apps/car-rental/base/`, so it applies at whatever replica count an overlay sets. `ScheduleAnyway`, not `DoNotSchedule`: with only 2 workers, a hard constraint would leave pods `Pending` the moment one worker is full or briefly unavailable, which defeats the purpose on a cluster this size.
- **`PodDisruptionBudget`** (`minAvailable: 1`) for the same three, **prod-overlay-only** (`apps/car-rental/overlays/prod/pdb/`) — a PDB with `minAvailable: 1` against a 1-replica dev Deployment would block voluntary disruptions (node drains, `oc adm cordon`) entirely, so it's deliberately not in the shared base.
- **Not done, and why:** pod anti-affinity was considered and rejected in favor of `topologySpreadConstraints` — anti-affinity's binary "never/always" co-locate rule is a worse fit than spread's proportional balancing on a 2-node pool, and OpenShift/Kubernetes upstream guidance has moved the same direction.

## LLD 02 · Service specifications

| Service | Image | Port | Store | Notes |
|---|---|---|---|---|
| `catalog-svc` | car-rental-svc:latest | 8080 | Postgres · `catalog` (seed also in-memory) | 5 cars, rate/km seeded |
| `location-svc` | car-rental-svc:latest | 8080 | Postgres · `location` (seed also in-memory) | 5 cities, 20 directed routes |
| `traveler-svc` | car-rental-svc:latest | 8080 | Postgres · `traveler` | name/age editable post-creation |
| `pricing-svc` | car-rental-svc:latest | 8080 | stateless | fare = rate/km × distance |
| `booking-svc` | car-rental-svc:latest | 8080 | Postgres · `booking` | the one orchestrator |
| `payment-svc` | car-rental-svc:latest | 8080 | Postgres · `payment` | mock capture + ledger |
| `chatbot-svc` | car-rental-svc:latest | 8080 | Redis (session) | talks to Ollama + mcp-server |
| `mcp-server` | car-rental-svc:latest | 8080 | none | wraps catalog/location/booking as tools |
| `api-gateway` | nginx-unprivileged:1.27-alpine | 8080 | none | reverse-proxy, one Route |
| `web-ui` | nginx-unprivileged:1.27-alpine | 8080 | none | static page, one Route |
| `postgresql` | postgres:16-alpine | 5432 | 2Gi PVC (`nfs-storage`) | 5 schemas, one instance |
| `redis` | redis:7-alpine | 6379 | ephemeral | cache + chat session |
| `ollama` | ollama/ollama:latest | 11434 | 4Gi PVC (`nfs-storage`) | model pulled post-boot, out-of-band |

> ℹ️ Eight business services intentionally share one scaffold image (`ghcr.io/esarath/car-rental-svc`, source at `services/generic-svc/` in this repo — a minimal Flask app) differentiated by `SERVICE_NAME` and seed data — a documented POC simplification so the deployment wiring is real and testable before each service's business logic is written out. Splitting any one into its own image later touches only that Deployment's `image:` and CI job, nothing else.
>
> ⚠️ **Storage class note:** the cluster's only StorageClass (`nfs-storage`) has `allowVolumeExpansion: false` — the Postgres (2Gi) and Ollama model (4Gi) PVCs cannot be resized in place later. Sized with headroom for the POC's current scope; a larger Ollama model would need a new PVC + data migration, not an expansion.
>
> ⚠️ **Real gotcha, hit on this cluster (fixed 2026-09-04, PR #8):** `api-gateway`'s nginx config proxies to backend services by bare short name (`http://catalog-svc:8080`, etc.), which worked fine for every *other* resolver in the pod but not nginx's own — nginx's `resolver` directive does its own DNS queries and does **not** honor the pod's `/etc/resolv.conf` search-domain list the way glibc does, so a bare `catalog-svc` got a real NXDOMAIN from CoreDNS (`error 3: Host not found` in nginx's error log — a genuine answer, not a timeout, so this can look like a backend problem rather than a DNS one). Fixed by switching every `set $upstream` target to a fully-qualified name (`<svc>.${POD_NAMESPACE}.svc.cluster.local`). Since `nginx-configmap.yaml` is one shared base file for both the dev and prod overlays, the namespace segment can't be hardcoded — it's rendered via the `nginxinc/nginx-unprivileged` image's built-in envsubst template mechanism (`/etc/nginx/templates/*.template` → `conf.d/*.conf` at container startup) with a `POD_NAMESPACE` downward-API env var on the Deployment. If any future nginx-fronted service in this repo needs to reach another cluster-local Service, use an FQDN in its `resolver`-based `proxy_pass`, not a bare name.
>
> ⚠️ **Arbitrary-UID compatibility:** `postgresql`, `redis` and `ollama` run upstream (non-OpenShift-optimized) images under the `restricted` Pod Security profile with no `runAsUser`/`fsGroup` pinned, letting OpenShift's `restricted` SCC auto-assign both from the namespace's allocated UID range. Postgres mitigates the common failure mode by pointing `PGDATA` at a subdirectory of the mount (so `initdb` creates it fresh with correct ownership) rather than the mount root. If any of the three still `CrashLoopBackOff` on first sync, see the execution guide's Phase 03 troubleshooting note before assuming the manifest is wrong.

## LLD 03 · Data model

One PostgreSQL instance, five schemas — the memory-saving tradeoff explained in the tenancy section above.

| Schema | Table | Key columns |
|---|---|---|
| `catalog` | `cars` | make, model, rate_per_km, active |
| `location` | `cities`, `routes` | from_city_id, to_city_id, distance_km |
| `traveler` | `travelers` | booking_id, name, age, gender (male/female) + derived `is_child` |
| `booking` | `bookings` | car_id, route_id, status, fare, created_at |
| `payment` | `payments` | booking_id, amount, method, status, created_at |

> ✅ **Fixed 2026-09-03:** the earlier draft's `gender (male/female/child)` conflated gender with an age bracket, flagged for product sign-off. Implemented directly in this pass — `gender` is now `male`/`female` only, with a separate `is_child BOOLEAN` column derived from `age` (see `apps/car-rental/base/postgresql/init-configmap.yaml`).

## LLD 04 · Network & security

**NetworkPolicy (both namespaces, 6 objects each)** — `default-deny-all` (ingress+egress), then explicit allows: `allow-same-namespace` (pod-to-pod), `allow-from-openshift-ingress` (router → api-gateway/web-ui only), `allow-from-monitoring` (cluster + user-workload scrape), `allow-dns-egress`, and `allow-ollama-model-pull-egress` — scoped to the `ollama` pod label specifically, not a blanket namespace egress rule.

> ⚠️ **Real gotcha, hit on this cluster (fixed 2026-09-04, PR #7):** `allow-dns-egress` allowing just UDP/TCP port 53 looked correct but silently broke *all* DNS resolution for every pod in the namespace — the policy's "allow" never actually matched. Root cause: this cluster's DNS operator runs CoreDNS on containerPort **5353**, not 53 (the `dns-default` Service maps external port 53 → targetPort 5353 to avoid running CoreDNS as root), and OVN-Kubernetes evaluates egress NetworkPolicy ACLs *after* load-balancing resolves the Service to its backend pod — so the ACL is checked against the real post-NAT port (5353), not the Service's declared port (53). The policy needs **both** 53 and 5353 for UDP and TCP. If DNS silently times out for every pod in a namespace with this policy pattern, check `oc get svc dns-default -n openshift-dns -o yaml` and the CoreDNS pod's actual `containerPort` before suspecting anything else — and reach for OVN's own ACL audit log (`oc annotate namespace <ns> k8s.ovn.org/acl-logging='{"deny":"info","allow":"info"}'`, then read `/var/log/ovn/acl-audit-log.log` in any `ovnkube-node` pod's `ovn-acl-logging` container) immediately; it shows the real per-packet verdict and port, and is far faster than `ovn-trace`/`ofproto/trace` simulation or manual packet capture.

**Secrets** — Postgres credentials ship as a placeholder `Secret` today — acceptable for a lab POC, flagged to move to Sealed Secrets or the External Secrets Operator before this goes further.

## LLD 05 · CI/CD pipeline

```
git push (main, services/generic-svc/**) → GH Actions CI (smoke-import·build·push) → ghcr.io (SHA-tagged image)
    → CI opens a PR bumping the dev overlay's tag → 1 required approval → merge → ArgoCD sync (car-rental-dev)
    → promote (manual workflow_dispatch, tag must match what's live in dev) → opens a PR bumping the prod overlay
    → 1 required approval → merge → ArgoCD sync (car-rental-prod)
```

**No step writes to `main` directly, including the automation itself.** `car-rental-ci.yaml` builds/pushes the image and opens a PR for the dev tag bump — it does not commit straight to `main`. `car-rental-promote.yaml` is the deliberate, human-triggered step into production (`workflow_dispatch`, refuses to promote a tag that isn't the one currently deployed in dev) and *also* only opens a PR, never pushes directly. Every PR — from a person or from either bot — is gated by branch protection on `main`:

- Require a pull request before merging (no direct pushes, no exceptions)
- Require **1** approving review (CODEOWNERS: `@esarath`) — dismissed on new commits
- Require the `validate-car-rental` status checks to pass before merge is allowed
- No self-approval, no auto-merge configured anywhere in this repo

`validate-car-rental.yaml` gates every PR/push with yaml-lint + `kubectl kustomize` build + a `kind`-backed server-side dry-run of both overlays — this is the required status check branch protection points at.

> ⚠️ **Branch protection itself is not yet configured** — it has to be set via the GitHub API/UI with repo-admin rights, which is outside what this session could safely automate (the same PAT-handling restriction that blocked automated repo creation earlier — see the execution guide's setup step for the exact command to run). The workflow YAML above only has teeth once that setting exists.

## LLD 06 · AI / MCP chatbot

`chatbot-svc` receives a message, calls Ollama with the MCP tool schema attached, executes whichever tool the model picks (`list_cars`, `get_route_distance`, `create_booking`, `get_booking_status`) via `mcp-server`, and returns the model's natural-language reply. The one write tool, `create_booking`, is gated behind an explicit confirmation turn.

> ℹ️ If Ollama's memory footprint proves too tight in practice, the fallback is a rules/intent-matching chatbot built against the same MCP tool layer — no redesign needed to swap a real LLM back in later.

---

# Deployment

## Deploy 01 · First-sync → production sequence

Unlike extending an already-live GitOps repo, this is a **brand-new** `app-of-apps` — nothing auto-syncs until the first manual bootstrap.

1. **Bootstrap (one-time, manual)** — `oc apply -f apps/app-of-apps/project.yaml` then `oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops`. From here on, everything below is ArgoCD-automatic except the two manual gates.
2. **Tenant namespace bootstraps** — `car-rental-app-of-apps` syncs `apps/app-of-apps/`, which creates both namespaces, both tenant Applications, and the `car-rental` AppProject's destinations.
3. **Manual quota apply** (cluster-admin, out-of-band) — the ArgoCD controller's service account cannot create `ResourceQuota`/`LimitRange` by design; apply once, by hand, per namespace.
4. **Workload syncs into dev** — `car-rental-dev` Application deploys `apps/car-rental/overlays/dev/` into the now-quota'd namespace.
5. **Pull the model, smoke-test** — pull the Ollama model once; walk a booking through `api-gateway`'s Route end to end.
6. **Repeat for production** — only after dev is healthy: apply the prod quota, then run the promote workflow with the validated image tag.

## Deploy 02 · Runbook commands

```bash
# 0 — one-time bootstrap (this repo is new — nothing syncs before this)
oc apply -f apps/app-of-apps/project.yaml
oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops

# 1 — confirm the tenant Applications synced
oc get application car-rental-dev car-rental-prod -n openshift-gitops

# 2 — manual quota / limits (cluster-admin, out-of-band)
oc apply -f platform/multi-tenancy/manual/car-rental-dev/resourcequota.yaml
oc apply -f platform/multi-tenancy/manual/car-rental-dev/limitrange.yaml

# 3 — confirm the workload synced
oc get pods -n car-rental-dev
oc get route -n car-rental-dev

# 4 — pull the POC model once
oc exec deploy/ollama -n car-rental-dev -- ollama pull llama3.2:1b

# 5 — check real usage before promoting to prod
oc adm top pods -n car-rental-dev

# 6 — promote to production (after dev sign-off)
oc apply -f platform/multi-tenancy/manual/car-rental-prod/resourcequota.yaml
oc apply -f platform/multi-tenancy/manual/car-rental-prod/limitrange.yaml
gh workflow run car-rental-promote.yaml -f image_tag=<validated-sha>
```

## 03 · Risks & recommendations

| Risk | Impact | Recommendation |
|---|---|---|
| CPU headroom tighter than memory headroom | Dev+prod combined requests leave little CPU slack (~3.7 vCPU free vs ~3.5 vCPU needed) | Check `oc adm top pods`/`oc describe nodes` for CPU, not just memory, before enabling prod load |
| Upstream (non-OpenShift-optimized) images under `restricted` SCC | postgresql/redis/ollama could fail to start on arbitrary-UID edge cases | PGDATA-subdirectory mitigation already applied; if a pod still CrashLoopBackOffs, check UID/permission errors first (see LLD 02) |
| `nfs-storage` StorageClass has `allowVolumeExpansion: false` | Postgres/Ollama PVCs can't grow in place | Size PVCs with margin up front; plan a new-PVC-and-migrate path if outgrown |
| Placeholder Postgres Secret committed in Git | Credential exposure | Move to Sealed Secrets / External Secrets before anything beyond this POC |
| Shared scaffold image across 8 services | No real business logic yet | Split services out one at a time, each with its own image + CI job, as logic is written |
| Ollama egress is `0.0.0.0/0` on 443, though pod-scoped | Still a broad destination CIDR for a model pull | Narrow to the real egress proxy / allowlist CIDR once known |
| No HPAs configured | Not currently applicable — cluster-wide HPA list is empty as of 2026-09-03 (the earlier-flagged failing HPA belonged to a workload since torn down) | Revisit only if/when HPAs are actually introduced for this workload |
| No ServiceMonitors on car-rental services | No scrape-based alerting yet (PDBs are now in place — see LLD 01a) | Add once dev is stable, mirroring `ocp-gitops-poc`'s own `argocd/components/` pattern |
| ResourceQuota/LimitRange stay manual, not ArgoCD-managed | The one deliberate exception to "everything managed by ArgoCD" | **Kept intentionally.** Granting the ArgoCD controller SA `create` on `ResourceQuota`/`LimitRange` would let a compromised or misconfigured Application self-grant more quota than intended — the same reasoning already established for `ocp-gitops-poc`'s multi-tenancy work. Every other object (namespaces, NetworkPolicies, RBAC, all 12 workloads, PDBs) is 100% ArgoCD-managed. Revisit only with an explicit, separate risk-acceptance decision. |
| Branch protection not yet configured on GitHub | The PR-approval workflow (LLD 05) has no teeth until this is set | See execution guide's setup step — needs repo-admin API access this session can't safely automate (same PAT restriction as repo creation) |

---

*Car Rental Booking Platform · Architecture Design Record*
*Source: esarath/ocp-argocd-vechicle-booking-poc · branch main*
*Reviewed against live cluster state (lab.ocp.local, OCP 4.20.35) on 2026-09-03*
