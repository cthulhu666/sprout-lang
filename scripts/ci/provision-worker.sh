#!/usr/bin/env bash
# Provision the e2-standard-4 CI worker: swap + toolchain + a forgejo-runner
# brought online with the SAME labels as the existing runner, capacity 2.
#
# Idempotent: safe to re-run. Run ON THE WORKER as the login user (must have
# passwordless sudo, like the existing runner box).
#
# Codeberg's "Create new Runner" (repo -> Settings -> Actions -> Runners)
# pre-creates the runner server-side and shows a (UUID, token) PAIR. The daemon
# authenticates with that pair via --uuid + --token-url. There is NO `register`
# step: that verb belongs to a separate registration-token flow that Codeberg
# rejects ("registration token not found"). Supply the pair by env or file
# (file form keeps secrets off the command line):
#   FORGEJO_RUNNER_UUID=...  FORGEJO_RUNNER_TOKEN=...  ./provision-worker.sh
#   # or place them in ~/reguuid and ~/regtoken
#
# Mirrors the live runner: forgejo-runner v12.10.1, user-run daemon, working
# dir /var/lib/forgejo-runner, labels self-hosted:host + linux-x86_64:host.
set -euo pipefail

# UUID + token may come from env or a file. Env wins when set.
if [ -z "${FORGEJO_RUNNER_UUID:-}" ]; then
  f="${FORGEJO_RUNNER_UUID_FILE:-$HOME/reguuid}"; [ -f "$f" ] && FORGEJO_RUNNER_UUID="$(< "$f")"
fi
if [ -z "${FORGEJO_RUNNER_TOKEN:-}" ]; then
  f="${FORGEJO_RUNNER_TOKEN_FILE:-$HOME/regtoken}"; [ -f "$f" ] && FORGEJO_RUNNER_TOKEN="$(< "$f")"
fi
: "${FORGEJO_RUNNER_UUID:?set FORGEJO_RUNNER_UUID or ~/reguuid (from Codeberg Create new Runner)}"
: "${FORGEJO_RUNNER_TOKEN:?set FORGEJO_RUNNER_TOKEN or ~/regtoken (from Codeberg Create new Runner)}"
RUNNER_VERSION="${RUNNER_VERSION:-12.10.1}"
INSTANCE_URL="${INSTANCE_URL:-https://codeberg.org/}"
LABELS="${LABELS:-self-hosted:host,linux-x86_64:host}"
CAPACITY="${CAPACITY:-2}"
SWAP_GB="${SWAP_GB:-16}"
WORKDIR=/var/lib/forgejo-runner
TOKEN_DST=/etc/forgejo-runner/token
CFG=/etc/forgejo-runner/config.yaml
RUN_USER="$(whoami)"

echo "== 1. Swap (${SWAP_GB}G) — OOM insurance for two parallel clang -O2 bootstraps =="
if ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l "${SWAP_GB}G" /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
  echo "swap already present — skipping"
fi

echo "== 2. Toolchain + node (clang-16/llvm-16 re-asserted by CI; node runs JS"
echo "      actions like actions/checkout@v4 — required on a system path) =="
sudo apt-get update -y
sudo apt-get install -y clang-16 llvm-16 ripgrep git curl jq nodejs

echo "== 3. mise + just (CI expects mise-managed just on PATH) =="
if ! command -v mise >/dev/null; then
  curl -fsSL https://mise.run | sh
fi
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
mise use -g just@1.39.0

echo "== 4. forgejo-runner v${RUNNER_VERSION} binary =="
if ! forgejo-runner --version 2>/dev/null | grep -q "$RUNNER_VERSION"; then
  url="https://code.forgejo.org/forgejo/runner/releases/download/v${RUNNER_VERSION}/forgejo-runner-${RUNNER_VERSION}-linux-amd64"
  tmp="$(mktemp)"
  curl -fsSL "$url" -o "$tmp"
  sudo install -m0755 "$tmp" /usr/local/bin/forgejo-runner
  rm -f "$tmp"
fi
forgejo-runner --version

echo "== 5. Runner token file (${TOKEN_DST}, 0600, ${RUN_USER}) =="
sudo mkdir -p "$WORKDIR" /etc/forgejo-runner
sudo chown "$RUN_USER:" "$WORKDIR"
printf '%s' "$FORGEJO_RUNNER_TOKEN" | sudo tee "$TOKEN_DST" >/dev/null
sudo chmod 600 "$TOKEN_DST"
sudo chown "$RUN_USER:" "$TOKEN_DST"

echo "== 6. config.yaml with capacity ${CAPACITY} (concurrent test + test-ir) =="
forgejo-runner generate-config | sudo tee "$CFG" >/dev/null
sudo sed -i -E "s/^(\s*capacity:).*/\1 ${CAPACITY}/" "$CFG"
grep -nE '^\s*capacity:' "$CFG" || echo "WARN: capacity key not found — check $CFG"

echo "== 7. systemd unit (daemon authenticates via --uuid + --token-url) =="
LABEL_FLAGS=""
IFS=',' read -ra _labels <<< "$LABELS"
for l in "${_labels[@]}"; do LABEL_FLAGS+=" --label $l"; done
sudo tee /etc/systemd/system/forgejo-runner.service >/dev/null <<UNIT
[Unit]
Description=Forgejo Runner (Sprout CI worker)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/forgejo-runner daemon --config ${CFG} --url ${INSTANCE_URL} --uuid ${FORGEJO_RUNNER_UUID} --token-url file://${TOKEN_DST}${LABEL_FLAGS}
WorkingDirectory=${WORKDIR}
User=${RUN_USER}
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now forgejo-runner.service

echo
echo "== DONE =="
sleep 3
systemctl is-active forgejo-runner && echo "service active"
echo "Recent log (expect: declared successfully, labels [self-hosted linux-x86_64]):"
journalctl -u forgejo-runner -n 8 --no-pager || true
echo
echo "Verify the runner shows Active in Codeberg, then STOP this instance:"
echo "  gcloud compute instances stop sprout-ci-worker-x86 --project spry-sequence-341 --zone europe-west1-b"
