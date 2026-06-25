#!/usr/bin/env bash
# Provision the e2-standard-4 CI worker: swap + toolchain + a forgejo-runner
# registered with the SAME labels as the existing runner, capacity 2.
#
# Idempotent: safe to re-run. Run ON THE WORKER as the login user (must have
# passwordless sudo, like the existing runner box).
#
# Requires a fresh Codeberg runner registration token:
#   Codeberg repo -> Settings -> Actions -> Runners -> "Create new Runner".
# Pass it in the environment (never commit it):
#   FORGEJO_REG_TOKEN=xxxxxxxx ./provision-worker.sh
#
# Mirrors the live runner: forgejo-runner v12.10.1, user-run daemon, working
# dir /var/lib/forgejo-runner, labels self-hosted:host + linux-x86_64:host.
set -euo pipefail

: "${FORGEJO_REG_TOKEN:?set FORGEJO_REG_TOKEN (Codeberg: Settings -> Actions -> Runners -> Create new Runner)}"
RUNNER_VERSION="${RUNNER_VERSION:-12.10.1}"
RUNNER_NAME="${RUNNER_NAME:-sprout-ci-worker-x86}"
INSTANCE_URL="${INSTANCE_URL:-https://codeberg.org/}"
LABELS="${LABELS:-self-hosted:host,linux-x86_64:host}"
CAPACITY="${CAPACITY:-2}"
SWAP_GB="${SWAP_GB:-16}"
WORKDIR=/var/lib/forgejo-runner
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

echo "== 2. Toolchain (clang-16/llvm-16 also re-asserted by CI; pre-baking = fast no-op) =="
sudo apt-get update -y
sudo apt-get install -y clang-16 llvm-16 ripgrep git curl jq

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

echo "== 5. Register runner (writes .runner with the server-assigned UUID) =="
sudo mkdir -p "$WORKDIR"
sudo chown "$RUN_USER:" "$WORKDIR"
if [ ! -f "$WORKDIR/.runner" ]; then
  ( cd "$WORKDIR" && forgejo-runner register --no-interactive \
      --instance "$INSTANCE_URL" \
      --token "$FORGEJO_REG_TOKEN" \
      --name "$RUNNER_NAME" \
      --labels "$LABELS" )
else
  echo ".runner already exists — skipping registration"
fi

echo "== 6. config.yaml with capacity ${CAPACITY} (concurrent test + test-ir) =="
CFG=/etc/forgejo-runner/config.yaml
sudo mkdir -p /etc/forgejo-runner
forgejo-runner generate-config | sudo tee "$CFG" >/dev/null
# Set capacity under the `runner:` block.
sudo sed -i -E "s/^(\s*capacity:).*/\1 ${CAPACITY}/" "$CFG"
grep -nE '^\s*capacity:' "$CFG" || echo "WARN: capacity key not found — check $CFG"

echo "== 7. systemd unit (daemon reads .runner from WorkingDirectory) =="
sudo tee /etc/systemd/system/forgejo-runner.service >/dev/null <<UNIT
[Unit]
Description=Forgejo Runner (Sprout CI worker)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/forgejo-runner daemon --config ${CFG}
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
echo "Verify the runner is online in Codeberg: repo -> Settings -> Actions -> Runners"
echo "Then STOP this instance (from your admin gcloud):"
echo "  gcloud compute instances stop ${RUNNER_NAME} --project spry-sequence-341 --zone europe-west1-b"
