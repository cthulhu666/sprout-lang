# On-demand CI worker (GCE dispatcher)

Sprout's Codeberg CI runs on a self-hosted Forgejo runner. To avoid paying for a
large always-on VM, an **always-on dispatcher** (tiny VM) starts a **stop/start
worker** (`e2-standard-4`) only while there is queued CI work, and stops it once
the queue drains.

This doc is the architecture + runbook. The repo-side artifacts live in
`scripts/ci/`. The GCP/Codeberg provisioning steps are run by you (they touch
your project and account), and are spelled out below.

## Architecture

```
┌─ Dispatcher (e2-micro, always on) ─────────────────────────────┐
│  sprout-ci-dispatcher.timer → dispatcher.sh, every ~45s:       │
│    active = GET /repos/{owner}/{repo}/actions/runs             │
│             (count runs NOT in success/failure/cancelled/      │
│              skipped — i.e. queued/waiting/running)            │
│    active>0 & worker TERMINATED      → gcloud … start          │
│    active=0 for IDLE_GRACE_TICKS ticks & worker RUNNING        │
│                                       → gcloud … stop          │
└─────────────────────────────────────────────────────────────────┘
            │ compute.instances.start/stop (one instance)
            ▼
┌─ Worker (e2-standard-4, normally TERMINATED) ──────────────────┐
│  Boot disk persists across stop/start. Pre-provisioned once:   │
│  mise + just + clang-16 + llvm-16 + forgejo-runner, registered │
│  with labels self-hosted,linux-x86_64, capacity 2, + swapfile. │
│  systemd starts the runner daemon on boot; it drains the queued│
│  jobs, then the dispatcher stops it.                           │
└─────────────────────────────────────────────────────────────────┘
```

Nothing in `.forgejo/workflows/*.yml` changes: the worker advertises the same
`runs-on: [self-hosted, linux-x86_64]` labels the jobs already target.

### Why poll `actions/runs`, not `actions/tasks`

The dispatcher must detect work that is **waiting for a runner** — that is the
trigger to boot one. The `actions/tasks` endpoint is blind to this: a *task* is
a runner-assigned unit, so a job with no available runner never appears there
until a runner exists (go-gitea/gitea#35134) — a chicken-and-egg dead end. A
*run* is created by the trigger event itself, before any runner, so
`actions/runs` reflects queued work. The script classifies by a **terminal
denylist** (`success/failure/cancelled/skipped` = done; everything else = active)
so an unknown/new status fails safe toward keeping the worker up, never toward
stopping mid-job.

> **Confirm once before relying on it** (see Verification §1): that Codeberg's
> Forgejo build lists a run while it is still queued. If it does not, switch the
> trigger to a push/PR webhook (see Alternatives).

## One-time setup

### 0. Prerequisites
- `gcloud` authenticated to your project; pick a `ZONE` (e.g. `europe-west1-b`).
- A Codeberg access token with `read:repository` scope (dispatcher → API).
- A Codeberg **runner registration token**: repo → Settings → Actions → Runners
  → "Create new Runner" (worker → registers once).

### 1. Worker instance (provision, then leave stopped)

```sh
PROJECT=your-project; ZONE=europe-west1-b
WORKER=sprout-ci-worker-x86

gcloud compute instances create "$WORKER" \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type e2-standard-4 \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 40GB --boot-disk-type pd-balanced \
  --no-service-account --no-scopes        # worker needs no GCP perms
```

SSH in and provision the toolchain + runner **once** (persists on the boot disk):

```sh
# Toolchain (clang-16/llvm-16 are also re-asserted by the workflow; pre-baking
# makes that a fast no-op instead of a multi-minute install each run).
sudo apt-get update
sudo apt-get install -y clang-16 llvm-16 ripgrep git curl jq
curl https://mise.run | sh        # provides `just` per mise.toml

# Swapfile — OOM insurance so the two parallel bootstraps (test + test-ir,
# each clang -O2 over ~298k IR lines) can peak past 16 GB without an OOM kill.
sudo fallocate -l 16G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Dedicated runner user. The workflows call `sudo apt-get`, so it needs
# passwordless sudo — mirror however your *current* runner is set up.
sudo useradd -m -d /home/runner -s /bin/bash runner
echo 'runner ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/runner
sudo install -m0755 forgejo-runner /usr/local/bin/forgejo-runner   # release binary

# Register ONCE, AS THE runner USER, so `.runner` lands in /home/runner (where
# the daemon's WorkingDirectory points). Mirror the EXECUTOR and LABELS of your
# existing runner — `:host` below assumes the current runner runs jobs on the
# host (it must, since the workflows `sudo apt-get install`). If your current
# runner uses docker, use `:docker` instead.
sudo -u runner -H bash -c 'cd ~ && forgejo-runner register --no-interactive \
  --instance https://codeberg.org \
  --token "<RUNNER_REGISTRATION_TOKEN>" \
  --name sprout-ci-worker-x86 \
  --labels "self-hosted:host,linux-x86_64:host"'
# then set `runner.capacity: 2` in /home/runner/.runner (concurrent test+test-ir)

# systemd: start the runner daemon on every boot (so stop/start "just works").
sudo tee /etc/systemd/system/forgejo-runner.service >/dev/null <<'UNIT'
[Unit]
Description=Forgejo Runner
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/forgejo-runner daemon
WorkingDirectory=/home/runner        # where .runner was written above
User=runner
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable --now forgejo-runner.service

# Confirm one CI run drains, then stop — this is the resting state.
gcloud compute instances stop "$WORKER" --project "$PROJECT" --zone "$ZONE"
```

### 2. Dispatcher IAM (least privilege)

A custom role granting only get/start/stop, bound to the dispatcher VM's service
account, conditioned to the single worker instance:

```sh
gcloud iam roles create sproutCiDispatcher --project "$PROJECT" \
  --title "Sprout CI dispatcher" \
  --permissions compute.instances.get,compute.instances.start,compute.instances.stop

# Dedicated SA for the dispatcher VM.
gcloud iam service-accounts create sprout-ci-dispatcher \
  --project "$PROJECT" --display-name "Sprout CI dispatcher"
SA="sprout-ci-dispatcher@$PROJECT.iam.gserviceaccount.com"

# Bind the role, scoped to just the worker instance via an IAM condition.
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:$SA" \
  --role "projects/$PROJECT/roles/sproutCiDispatcher" \
  --condition "expression=resource.name.endsWith('/instances/$WORKER'),title=worker-only"
```

### 3. Dispatcher VM + service

```sh
gcloud compute instances create sprout-ci-dispatcher \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type e2-micro \
  --image-family debian-12 --image-project debian-cloud \
  --service-account "$SA" \
  --scopes https://www.googleapis.com/auth/cloud-platform
```

On the dispatcher VM:

```sh
sudo apt-get update && sudo apt-get install -y curl jq google-cloud-cli
sudo install -D -m0755 dispatcher.sh /opt/sprout-ci/dispatcher.sh
sudo install -m0644 sprout-ci-dispatcher.service sprout-ci-dispatcher.timer \
  /etc/systemd/system/
sudo install -m0600 dispatcher.env.example /etc/sprout-ci-dispatcher.env
sudoedit /etc/sprout-ci-dispatcher.env     # fill in token, project, zone, worker
sudo systemctl daemon-reload
sudo systemctl enable --now sprout-ci-dispatcher.timer
```

## Verification

1. **Queued jobs SURVIVE with the worker offline (the go/no-go for the whole
   design).** Visibility is not enough — the run must *stay queued without
   failing* long enough for the worker to boot (~1–2 min). If Forgejo fails-fast
   with "no matching runner" when the registered runner is merely offline, this
   architecture cannot work (and the webhook fallback can't save it — the run is
   created in <1s, faster than any boot). Test it directly:
   ```sh
   # Worker STOPPED. Push a trivial commit, then watch for ≥3 min:
   curl -fsS -H "Authorization: token $TOKEN" \
     "https://codeberg.org/api/v1/repos/$OWNER/$REPO/actions/runs?limit=5" \
     | jq '.workflow_runs[] | {status, head_branch}'
   ```
   PASS = the new run holds a non-terminal `status` for ≥3 min and does **not**
   go to `failure`. Then start the worker by hand and confirm it gets picked up.
   Also **eyeball whether `status` is a string or an int** — the dispatcher's
   filter handles both, but this confirms which Codeberg emits.
2. **IAM condition actually bound.** Compute Engine resource-level IAM conditions
   don't honor every permission cleanly; a silently no-op'd condition fails
   closed (CI hangs) or open (over-broad grant). As the dispatcher SA, confirm
   you *can* start the worker and *cannot* start a different instance:
   ```sh
   gcloud compute instances start "$WORKER" --zone "$ZONE"          # must succeed
   gcloud compute instances start some-other-vm --zone "$ZONE"      # must be denied
   ```
3. **Start path:** `journalctl -u sprout-ci-dispatcher.service -f` should log
   `active_runs>0 … starting worker`; the worker reaches `RUNNING` and the job
   runs.
4. **Stop path:** after the run finishes, ~`IDLE_GRACE_TICKS × 45s` later the log
   shows `stopping worker (idle …)` and the worker returns to `TERMINATED`.
5. **Concurrency holds:** confirm one boot drains *both* `test` and `test-ir`
   without an OOM kill (`dmesg | grep -i oom` on the worker). Swap absorbs the
   peak; if it still OOMs, raise swap or drop `runner.capacity` to 1.

## Operational notes

- **Latency:** ~poll interval + boot + runner-online ≈ 1–2 min added per CI run.
- **Cost:** dispatcher e2-micro (~free-tier/low single digits per month) +
  worker billed only while running (~$0.13/hr) + stopped boot disk (~$1.50/mo).
- **Fail-safe:** the dispatcher only start/stops, never create/deletes. A crashed
  dispatcher leaves the worker stopped and jobs queued — cost goes to zero, never
  runaway. `MAX_UPTIME_MIN` is an opt-in hard cap for a wedged job.

## Out of scope (v1)

- **aarch64.** `release.yml` has a `linux-aarch64` job; `e2-standard-4` is
  x86_64-only. Either add a parallel `t2a-standard-4` worker mirroring this setup
  (extend the dispatcher to manage a second instance), or keep that one job on an
  existing arm runner. Not handled here.

## Alternatives (if Verification §1 fails)

- **Push/PR webhook → dispatcher listener.** Add a repo webhook on `push` +
  `pull_request` pointing at a small HTTP endpoint on the dispatcher (validate
  the Gitea HMAC signature, then run the start path). Fires on the SCM event
  itself, fully independent of the Actions API — most robust, but adds an inbound
  endpoint (public port, firewall rule, secret). Teardown stays poll-based on
  `running` runs, which `actions/runs`/`actions/tasks` both report reliably.
- **Per-job ephemeral runners.** Forgejo 15 supports `forgejo-runner one-job
  --ephemeral` (auto-deregisters after one job). Gives each job a pristine VM at
  the cost of per-boot registration and a custom image or cold toolchain install.
  Higher isolation; more moving parts than stop/start.
```