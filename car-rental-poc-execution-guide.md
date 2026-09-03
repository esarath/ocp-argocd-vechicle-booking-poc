# Car Rental POC — LLD Execution Guide

**Companion to:** Car Rental POC Architecture (HLD/LLD)
**Target:** lab.ocp.local · OCP 4.20.35
**Repo:** esarath/ocp-argocd-vechicle-booking-poc · branch `main` (self-contained — its own `app-of-apps`, `AppProject`, CI)

Step-by-step build instructions against the published HLD/LLD: bootstrap this repo's own ArgoCD app-of-apps, onboard the tenant namespaces, apply governance, sync the workload through ArgoCD, validate in dev, then promote to production under a manual gate. Every task states who runs it, what to run, and how to confirm it worked before moving on.

---

## 00 · Before you start

**Access**
- `oc` CLI authenticated as cluster-admin (for the manual quota step and the one-time bootstrap)
- Merge/write access to `esarath/ocp-argocd-vechicle-booking-poc`
- ArgoCD console login (`openshift-gitops-server` route)
- `gh` CLI authenticated, for the promote workflow

**Cluster state — verified 2026-09-03, before relying on any of this:**
- All 5 nodes `Ready`, all 34 ClusterOperators `Available=True`/`Degraded=False`
- Real free worker capacity: ~6.5Gi memory / ~3.7 vCPU combined (see architecture doc LLD 01)
- Cluster-wide HPA list is empty — no failing-metrics HPA currently exists
- ✅ **Cleaned up 2026-09-03:** `redis-bench-loop` (leftover pod, `default` ns) and the unused `redis-platform` namespace / `redis-platform-appl` Application / `redis-project` AppProject (governance-only scaffold left over from an already-torn-down Redis workload) are all deleted — no open preconditions remain from earlier reviews.

**Artifacts on hand**
- This repo's `main` branch, containing `apps/`, `platform/multi-tenancy/manual/`, `services/generic-svc/`, and `.github/workflows/`
- `validate-car-rental.yaml` CI green on the commit you're deploying
- The Architecture doc (HLD/LLD) for cross-reference

> ⚠️ **Gate:** Do not start Phase 1 until CI (`validate-car-rental.yaml`: yaml-lint, kustomize build, kind-backed server-side dry-run) is green on the commit. That workflow is the safety net for everything below — skipping it means finding these mistakes live on the cluster instead.

---

## Phase -1 — One-time GitHub setup: branch protection (manual, repo-admin)
**Owner:** Repo owner (`esarath`) · **Mode:** manual, repo-admin — **✅ done 2026-09-03**

The whole "no auto-approve, min 1 approver" design in LLD 05 only has teeth once this exists — without it, both bots (and anyone else) can still push straight to `main`.

### Task -1a — Set branch protection on `main` `manual` — ✅ done
Applied via `gh api` with a JSON payload (the nested `-f`/`-F` flag syntax rejects typed fields like booleans/integers inside nested objects — use `--input` with a real JSON file instead):
```bash
cat > /tmp/branch-protection.json <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["yaml-lint", "kustomize-and-dry-run"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
gh api -X PUT repos/esarath/ocp-argocd-vechicle-booking-poc/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  --input /tmp/branch-protection.json
```
✓ **Verified live:** a direct `git push origin main` (an empty test commit) was rejected with `GH006: Protected branch update failed ... Changes must be made through a pull request ... 2 of 2 required status checks are expected.` The rule is real, not just declared.
✓ **Verify:** `gh api repos/esarath/ocp-argocd-vechicle-booking-poc/branches/main/protection | jq .required_pull_request_reviews`, and try a direct `git push origin main` — it should be rejected.

↺ **Rollback:** Settings misconfigured — re-run with corrected values, or delete the rule from **Settings → Branches** and start over; no effect on already-merged history.

---

## Phase 00 — Bootstrap this repo's ArgoCD wiring (one-time, manual)
**Owner:** Platform/GitOps engineer · **Mode:** manual — this repo is new, nothing auto-syncs before this

Unlike extending an already-live GitOps repo's `app-of-apps`, this repo owns its own from scratch. Nothing below happens until this step runs once.

### Task 0 — Apply the AppProject and root Application `manual`
```bash
oc apply -f apps/app-of-apps/project.yaml
oc apply -f apps/app-of-apps/app-of-apps.yaml -n openshift-gitops
```
✓ **Verify:** `oc get appproject car-rental -n openshift-gitops` and `oc get application car-rental-app-of-apps -n openshift-gitops` both exist.

↺ **Rollback:** Wrong values applied — fix the source YAML, `oc apply -f` again; both are declarative, no disruption from a re-apply.

---

## Phase 01 — Onboard the tenant namespaces
**Owner:** Platform/GitOps engineer · **Mode:** automatic (ArgoCD), following Phase 00

Once `car-rental-app-of-apps` exists, it syncs `apps/app-of-apps/` — namespaces, tenant Applications, NetworkPolicies, RoleBindings — on its own.

### Task 1 — Confirm the tenant Applications synced `automatic`
```bash
oc get application car-rental-dev car-rental-prod -n openshift-gitops \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```
✓ **Verify:** Both show `SYNC=Synced`, `HEALTH=Healthy`.

### Task 2 — Verify namespace governance landed `automatic`
```bash
oc get ns car-rental-dev car-rental-prod --show-labels
oc get networkpolicy -n car-rental-dev
oc get rolebinding -n car-rental-dev
```
✓ **Verify:** **6** NetworkPolicies in each namespace (`default-deny-all`, `allow-same-namespace`, `allow-from-openshift-ingress`, `allow-from-monitoring`, `allow-dns-egress`, `allow-ollama-model-pull-egress`); `car-rental-team-admin` RoleBinding present.

↺ **Rollback:** If a policy is missing or malformed: fix the source YAML, re-push — do not hand-patch the live object, ArgoCD's self-heal will revert it anyway.

---

## Phase 02 — Apply quota & limits (manual, out-of-band)
**Owner:** Cluster admin · **Mode:** manual — the one step ArgoCD deliberately cannot do

The ArgoCD application-controller service account's auto-granted namespace role explicitly excludes `create` on `ResourceQuota`/`LimitRange` — a namespace admin (even an automated one) must not be able to self-grant more quota. This is applied by hand, once per namespace.

### Task 3 — Apply the dev quota and limit range `manual`
Sized for 13 pods at 1 replica each (~1.4Gi requests / ~3.3Gi limits) with ~40% headroom.
```bash
oc apply -f platform/multi-tenancy/manual/car-rental-dev/resourcequota.yaml
oc apply -f platform/multi-tenancy/manual/car-rental-dev/limitrange.yaml
```
✓ **Verify:** `oc describe resourcequota car-rental-dev-quota -n car-rental-dev` shows the hard limits from the LLD table (requests.cpu 1.5, requests.memory 2Gi, pods 20).

### Task 4 — Apply the production quota and limit range `manual`
Can be applied now (idempotent, no workload exists yet in `car-rental-prod`) so it is not a blocker later at promote time.
```bash
oc apply -f platform/multi-tenancy/manual/car-rental-prod/resourcequota.yaml
oc apply -f platform/multi-tenancy/manual/car-rental-prod/limitrange.yaml
```
✓ **Verify:** `oc get resourcequota,limitrange -n car-rental-prod` returns both objects.

↺ **Rollback:** Wrong ceiling applied — re-apply the corrected file; `ResourceQuota`/`LimitRange` are declarative, a re-apply replaces `spec.hard`/`limits` in place with no pod disruption.

> **Gate:** Do not proceed to Phase 3 until **both** quotas show in `oc get resourcequota -A | grep car-rental` — the workload sync in Phase 3 will fail admission without them.

---

## Phase 03 — Sync the workload into dev
**Owner:** Platform/GitOps engineer · **Mode:** automatic (ArgoCD), verified manually

`car-rental-dev` Application deploys `apps/car-rental/overlays/dev/` — all 12 Deployments + 1 StatefulSet — into the now-quota'd namespace.

### Task 5 — Confirm the Application synced within quota `automatic`
If it synced *before* Phase 2's quota existed, ArgoCD will show a sync error on pod creation (`exceeded quota` or admission-denied) — re-check after Phase 2, no re-trigger needed since self-heal retries automatically.
```bash
oc get application car-rental-dev -n openshift-gitops
oc get pods -n car-rental-dev
```
✓ **Verify:** 13/13 pods `Running` (12 Deployments + 1 StatefulSet), each with `READY 1/1`.

### Task 6 — Check every readiness/liveness probe is passing `automatic`
```bash
oc get pods -n car-rental-dev -o wide
oc get events -n car-rental-dev --sort-by=.lastTimestamp | tail -30
```
✓ **Verify:** No `Unhealthy`, `CrashLoopBackOff`, or `FailedMount` events in the tail.

↺ **Rollback / troubleshooting:** A specific service crash-looping — `oc logs deploy/<svc> -n car-rental-dev`. Two known causes, check in this order:
1. The shared image isn't pushed yet (see Task 7) — `ImagePullBackOff`, not `CrashLoopBackOff`.
2. `postgresql`/`redis`/`ollama` specifically crash-looping with a permission/UID error — see architecture doc LLD 02's arbitrary-UID note; confirm the pod's actually-assigned UID with `oc get pod <name> -n car-rental-dev -o jsonpath='{.spec.securityContext}'` and check the mounted volume's ownership matches.

### Task 7 — Confirm the shared service image built, and merge the tag-bump PR `manual (the merge), automatic (everything before it)`
The 8 business services all run `ghcr.io/esarath/car-rental-svc`, built by `car-rental-ci.yaml` on first push to `services/generic-svc/**`. That workflow only *opens a PR* against the dev overlay — it does not push to `main` itself (see architecture doc LLD 05) — so a human still has to approve and merge it before ArgoCD sees a new tag.
```bash
gh run list --workflow=car-rental-ci.yaml --limit 3
gh api /user/packages/container/car-rental-svc/versions --jq '.[0].metadata.container.tags'
gh pr list --search "ci: car-rental-svc"
```
✓ **Verify:** Latest workflow run `completed`/`success`; a PR titled `ci: car-rental-svc <sha> -> dev` is open — review and merge it (requires the 1 approval from Phase -1's branch protection). Only after that merge does the dev overlay's `newTag` match a real pushed tag (not the `latest` placeholder it ships with).

---

## Phase 04 — Data & AI runtime bring-up
**Owner:** Platform/GitOps engineer · **Mode:** manual (one-time, application data — not GitOps-managed)

Two things ArgoCD correctly does not own: confirming the database seeded itself, and pulling the AI model.

### Task 8 — Confirm PostgreSQL schemas initialized `automatic on first boot`
`postgresql-init` ConfigMap runs once via `docker-entrypoint-initdb.d` on a fresh PVC.
```bash
oc exec -it postgresql-0 -n car-rental-dev -- psql -U carrental -d carrental -c "\dn"
oc exec -it postgresql-0 -n car-rental-dev -- psql -U carrental -d carrental -c "\dt catalog.*"
```
✓ **Verify:** 5 schemas listed (`catalog`, `location`, `traveler`, `booking`, `payment`); `catalog.cars` exists.

↺ **Rollback:** Schemas missing — the init script only runs against an empty `PGDATA`. Scale the StatefulSet to 0, `oc delete pvc data-postgresql-0 -n car-rental-dev`, then scale back up to re-trigger init (destructive — dev data only).

### Task 9 — Confirm reference data (cars, cities) is served `manual`
The scaffold service falls back to built-in seed data for `catalog-svc`/`location-svc` even without `SEED_DATA_JSON` set — confirm it, don't re-seed.
```bash
oc exec -it deploy/catalog-svc -n car-rental-dev -- curl -s localhost:8080/items | head -c 400
```
✓ **Verify:** Returns the 5 seeded cars (TATA/Harrier, Mahindra/XUV700, Honda/City, Force/Traveller, Maruti/Ertiga) with their `rate_per_km`.

### Task 10 — Pull the Ollama model `manual, one-time`
Ships with an empty model PVC by design — this is the one step that needs real outbound egress.
```bash
oc exec deploy/ollama -n car-rental-dev -- ollama pull llama3.2:1b
oc exec deploy/ollama -n car-rental-dev -- ollama list
```
✓ **Verify:** `llama3.2:1b` listed, ~1.3GB on the PVC.

↺ **Rollback:** Egress blocked — pull the model on a machine with internet, export the blob, load it via `ollama create`; or fall back to the rules-based chatbot path noted in the HLD (no redesign needed).

---

## Phase 05 — Dev validation
**Owner:** Platform engineer + app owner · **Mode:** manual

Walk the booking flow end to end through the real Route, and confirm the namespace is healthy under quota before anyone talks about production.

### Task 11 — Smoke-test through api-gateway's Route `manual`
```bash
ROUTE=$(oc get route api-gateway -n car-rental-dev -o jsonpath='{.spec.host}')
curl -sk https://$ROUTE/catalog/items | jq '.[0]'
curl -sk https://$ROUTE/booking/health
```
✓ **Verify:** Both return `200`; catalog item has a non-null `rate_per_km`.

### Task 12 — Confirm real usage sits inside quota headroom `manual`
```bash
oc adm top pods -n car-rental-dev
oc describe resourcequota car-rental-dev-quota -n car-rental-dev
```
✓ **Verify:** Used well under the ~40% headroom modeled in the LLD (requests.memory used < ~1.4Gi of the 2Gi hard cap). Check CPU here too, not just memory — see architecture doc's CPU headroom risk note.

↺ **Rollback:** Over budget — cut Ollama first (`oc scale deploy/ollama --replicas=0 -n car-rental-dev`); chatbot-svc/mcp-server degrade to non-AI stubs rather than failing, by design.

### Task 13 — Confirm NetworkPolicy isolation actually holds `manual`
Prove default-deny is real, not just declared — try a call that should be blocked.
```bash
# from a pod in a different namespace (e.g. default) — should time out
oc run netpol-test --rm -it --image=curlimages/curl -n default -- \
  curl -s --max-time 3 http://catalog-svc.car-rental-dev.svc.cluster.local:8080/health
```
✓ **Verify:** Times out (blocked cross-namespace) — same-namespace calls in Task 11 succeeded, cross-namespace calls here don't.

> **Gate to production:** all of Phase 5's tasks pass, **and** a human sign-off is recorded (app owner + platform lead) — this is a deliberate manual gate, not a policy the pipeline enforces for you.

---

## Phase 06 — Promote to production
**Owner:** App owner triggers, Platform engineer executes · **Mode:** manual trigger, automatic sync

The asymmetry is deliberate: dev moves automatically on every merge, production moves only when a human names a validated tag — and the promote workflow itself refuses a tag that isn't the one currently live in dev.

### Task 14 — Record the validated dev image tag `manual`
```bash
grep newTag apps/car-rental/overlays/dev/kustomization.yaml
```
✓ **Verify:** This is the exact SHA tag that passed Phase 5 — write it down, it's the promote input.

### Task 15 — Run the promote workflow, then merge the PR it opens `manual, two gates`
Two separate human actions, deliberately — dispatching the workflow is not the same as merging its PR:
```bash
gh workflow run car-rental-promote.yaml -f image_tag=<validated-sha>
gh run watch
gh pr list --search "promote: car-rental-svc"
```
✓ **Verify:** Workflow completes and opens a PR titled `promote: car-rental-svc <sha> -> prod`. Review and merge it (requires the 1 approval from Phase -1's branch protection) — only after that merge does `apps/car-rental/overlays/prod/kustomization.yaml` actually carry `<validated-sha>` on `main`.

### Task 16 — Watch ArgoCD sync production `automatic`
```bash
oc get application car-rental-prod -n openshift-gitops -w
```
✓ **Verify:** `SYNC=Synced`, `HEALTH=Healthy`; `booking-svc`, `api-gateway`, `web-ui` show 2/2 pods each.

↺ **Rollback:** Bad promote — re-run the promote workflow with the previous known-good tag; same mechanism, same audit trail, no manual namespace surgery.

---

## Phase 07 — Production validation
**Owner:** Platform engineer + app owner · **Mode:** manual

Repeat the dev checks against production, plus the two things only matter at 2 replicas.

### Task 17 — Repeat the smoke test and quota check against prod `manual`
```bash
ROUTE=$(oc get route api-gateway -n car-rental-prod -o jsonpath='{.spec.host}')
curl -sk https://$ROUTE/catalog/items | jq '.[0]'
oc adm top pods -n car-rental-prod
```
✓ **Verify:** Same result as Phase 5, Tasks 11–12, against `car-rental-prod`.

### Task 18 — Confirm both replicas of each 2x service are actually load-bearing `manual`
```bash
oc get endpoints booking-svc api-gateway web-ui -n car-rental-prod
```
✓ **Verify:** Each Service lists 2 ready endpoint IPs, not 1 — confirms the replica count took effect and both pods are actually Ready, not just scheduled.

### Task 18a — Confirm the two replicas actually landed on different workers, and the PDB is real `manual`
```bash
oc get pods -n car-rental-prod -l app=booking-svc -o wide
oc get pods -n car-rental-prod -l app=api-gateway -o wide
oc get pods -n car-rental-prod -l app=web-ui -o wide
oc get pdb -n car-rental-prod
```
✓ **Verify:** For each of the three services, the two pods' `NODE` column shows `worker-1.lab.ocp.local` and `worker-2.lab.ocp.local` — not both on the same node (confirms `topologySpreadConstraints` actually worked, not just that it was declared). All 3 PDBs show `ALLOWED DISRUPTIONS: 1`.

↺ **If both replicas landed on the same node:** not a failure by itself (`whenUnsatisfiable: ScheduleAnyway` allows it if the other worker was briefly full) — re-check after a few minutes; if it persists, check `oc describe node` on both workers for why one is being avoided.

### Task 19 — Combined footprint check across both namespaces `manual`
The one risk called out in the LLD: dev + prod together are the tightest against this lab cluster's free CPU, not memory.
```bash
oc adm top pods -n car-rental-dev -n car-rental-prod
oc describe nodes worker-1.lab.ocp.local worker-2.lab.ocp.local | grep -A3 "Allocated resources"
```
✓ **Verify:** Combined requests stay comfortably under free worker capacity — for **both** CPU and memory. If not, this is the trigger to move production onto separate/larger nodes rather than run both indefinitely on this lab cluster.

---

## Rollback matrix

| Failure point | Symptom | Rollback action |
|---|---|---|
| Phase -1 — branch protection misconfigured or later disabled | Bots or people can push straight to `main` | Re-run the `gh api` command with corrected values (or `gh api repos/esarath/ocp-argocd-vechicle-booking-poc/branches/main/protection` to check current state), or fix via Settings → Branches |
| Phase 00 — bootstrap applied with wrong repo URL/branch | AppProject/Application exist but never sync | Fix `apps/app-of-apps/project.yaml`/`app-of-apps.yaml`, re-apply — declarative |
| Phase 01 — bad NetworkPolicy/RBAC | ArgoCD sync error, pods can't reach DNS/monitoring | Fix source YAML in the repo, push — self-heal reverts any live hand-edit anyway |
| Phase 02 — wrong quota values | Pods stuck `Pending`, admission denied | Re-apply the corrected `resourcequota.yaml`/`limitrange.yaml` — declarative, no disruption |
| Phase 03 — image tag doesn't exist yet | `ImagePullBackOff` on all 8 business services | Wait for `car-rental-ci.yaml` to complete, or manually push a `latest` tag once |
| Phase 03 — postgresql/redis/ollama UID/permission crash-loop | `CrashLoopBackOff` with a permission-denied error in logs | See LLD 02's arbitrary-UID note; confirm assigned UID and volume ownership match |
| Phase 04 — Postgres init didn't run | Empty schemas, service 500s on read | Scale StatefulSet to 0, delete the PVC, scale back to 1 (dev-only, destructive) |
| Phase 05 — over quota under real load | `oc adm top` approaching hard caps (CPU most likely first) | Scale Ollama to 0 first; it's the largest single consumer and degrades gracefully |
| Phase 06 — bad production promote | Health checks failing post-sync | Re-run `car-rental-promote.yaml` with the last known-good SHA |

## RACI by phase

| Phase | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| -1 GitHub setup | Repo owner (`esarath`) | Repo owner | — | Platform lead |
| 00–02 Bootstrap + onboarding | Platform/GitOps engineer | Platform lead | Security (NetworkPolicy scope) | App owner |
| 03–05 Dev rollout | Platform/GitOps engineer | Platform lead | App owner (smoke test) | — |
| 06–07 Prod rollout | Platform/GitOps engineer | App owner (go/no-go) | Platform lead | Stakeholders |

---

*Car Rental Booking Platform · LLD Execution Guide · Companion to: Car Rental POC Architecture (HLD/LLD) · esarath/ocp-argocd-vechicle-booking-poc*
