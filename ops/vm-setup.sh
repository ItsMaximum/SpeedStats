#!/usr/bin/env bash
# One-time (idempotent) setup of the SpeedStats host. Run as a user with sudo, e.g. opc on the Oracle VM:
#
#   curl -fsSL https://raw.githubusercontent.com/ItsMaximum/SpeedStats/main/ops/vm-setup.sh | \
#       GH_REPO=ItsMaximum/SpeedStats RUNNER_TOKEN=<token> bash
#
# RUNNER_TOKEN comes from GitHub: repo -> Settings -> Actions -> Runners -> New self-hosted runner (Linux, ARM64);
# it is valid for an hour. After this script, everything else happens through GitHub Actions (deploy.yml,
# scrape.yml); no more ssh needed.
#
# What it does:
#   1. installs Docker (if missing) and lets this user run it
#   2. creates /opt/speedstats with data/ (the .env is written by the Deploy workflow from GitHub secrets)
#   3. installs RUNNERS (default 2) GitHub Actions self-hosted runners as systemd services (label: speedstats).
#      A runner takes one job at a time, so with two a Deploy does not wait for the weekly scrape. Re-run the
#      script with a fresh RUNNER_TOKEN to add a runner to an existing host.
#   4. starts the api + dozzle containers
# The existing cloudflared systemd service is left alone; add tunnel routes in the Zero Trust dashboard:
#   new.speedstats.app  -> http://localhost:8000
#   logs.speedstats.app -> http://localhost:9999   (put a Cloudflare Access policy in front of it)

set -euo pipefail

GH_REPO="${GH_REPO:-ItsMaximum/SpeedStats}"
BASE=/opt/speedstats
RUNNERS="${RUNNERS:-2}"
RUNNER_VERSION="${RUNNER_VERSION:-2.327.1}"
ARCH=$(uname -m)
case "$ARCH" in
  aarch64|arm64) RUNNER_ARCH=arm64 ;;
  x86_64) RUNNER_ARCH=x64 ;;
  *) echo "unsupported arch $ARCH" >&2; exit 1 ;;
esac

log() { printf '\n==> %s\n' "$*"; }

# 1. Docker -----------------------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "installing Docker"
  . /etc/os-release
  case "$ID" in
    ol|rhel|centos|rocky|almalinux)
      # get.docker.com does not support Oracle Linux; Docker's RHEL/CentOS repo works on OL8/9 (x86_64 and aarch64)
      sudo dnf -y install dnf-plugins-core
      sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    *)
      curl -fsSL https://get.docker.com | sudo sh
      ;;
  esac
fi
sudo systemctl enable --now docker
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  log "added $USER to the docker group (takes effect for new shells; this script uses sudo where needed)"
fi
DOCKER="sudo docker"
if docker info >/dev/null 2>&1; then DOCKER="docker"; fi

# 2. Layout -----------------------------------------------------------------------------------------------------
log "creating $BASE"
sudo mkdir -p "$BASE/data"
sudo chown -R "$USER:$USER" "$BASE"
if [ ! -f "$BASE/.env" ]; then
  # placeholder so docker compose can start; the Deploy workflow overwrites it from GitHub secrets
  echo '# written by the Deploy workflow (GitHub -> Settings -> Secrets and variables -> Actions)' > "$BASE/.env"
  chmod 600 "$BASE/.env"
fi

# 3. GitHub Actions runners --------------------------------------------------------------------------------------
# the first lives in runner/ (named after the host), the others in runner-N/ (named host-N)
for n in $(seq 1 "$RUNNERS"); do
  if [ "$n" = 1 ]; then RUNNER_DIR="$BASE/runner"; NAME="$(hostname)"; else RUNNER_DIR="$BASE/runner-$n"; NAME="$(hostname)-$n"; fi
  if [ -f "$RUNNER_DIR/.runner" ]; then
    log "runner $NAME already configured in $RUNNER_DIR"
    continue
  fi
  if [ -z "${RUNNER_TOKEN:-}" ]; then
    echo "RUNNER_TOKEN is required to register runner $NAME (GitHub -> Settings -> Actions -> Runners -> New)" >&2
    exit 1
  fi
  log "installing GitHub Actions runner $NAME ($RUNNER_VERSION, $RUNNER_ARCH)"
  mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz && rm runner.tar.gz
  ./config.sh --unattended --url "https://github.com/$GH_REPO" --token "$RUNNER_TOKEN" \
    --name "$NAME" --labels speedstats --work _work --replace
  sudo ./svc.sh install "$USER"
  sudo ./svc.sh start
  cd - >/dev/null
done

# 4. Containers -------------------------------------------------------------------------------------------------
log "starting api + dozzle"
cd "$BASE"
if [ ! -f docker-compose.yml ]; then
  curl -fsSL -o docker-compose.yml "https://raw.githubusercontent.com/$GH_REPO/main/docker-compose.yml"
fi
$DOCKER compose pull api dozzle || true
$DOCKER compose up -d api dozzle

log "done"
cat <<EOF

Next steps:
  1. GitHub -> Actions -> Deploy -> Run workflow: writes $BASE/.env from the repository secrets and starts the API
  2. Cloudflare Zero Trust -> Tunnels -> your tunnel -> add public hostnames:
       new.speedstats.app  -> http://localhost:8000
       logs.speedstats.app -> http://localhost:9999  (+ an Access policy)
  3. GitHub -> Actions -> "Weekly scrape" -> Run workflow (untick resume) for the first crawl,
     or bootstrap from R2 once snapshots exist: docker compose run --rm scraper bootstrap
EOF
