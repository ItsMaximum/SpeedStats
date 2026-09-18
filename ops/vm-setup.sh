#!/usr/bin/env bash
# One-time (idempotent) setup of the SpeedStats host. Run as a user with sudo, e.g. opc on the Oracle VM:
#
#   curl -fsSL https://raw.githubusercontent.com/ItsMaximum/SpeedStats/main/ops/vm-setup.sh | \
#       GH_REPO=ItsMaximum/SpeedStats RUNNER_TOKEN=<token> bash
#
# RUNNER_TOKEN comes from GitHub: repo -> Settings -> Actions -> Runners -> New self-hosted runner (Linux, ARM64).
# After this script, everything else happens through GitHub Actions (deploy.yml, scrape.yml); no more ssh needed.
#
# What it does:
#   1. installs Docker (if missing) and lets this user run it
#   2. creates /opt/speedstats with data/ and an .env you fill in once
#   3. installs the GitHub Actions self-hosted runner as a systemd service (label: speedstats)
#   4. starts the api + dozzle containers
# The existing cloudflared systemd service is left alone; add tunnel routes in the Zero Trust dashboard:
#   new.speedstats.app  -> http://localhost:8000
#   logs.speedstats.app -> http://localhost:9999   (put a Cloudflare Access policy in front of it)

set -euo pipefail

GH_REPO="${GH_REPO:-ItsMaximum/SpeedStats}"
BASE=/opt/speedstats
RUNNER_DIR="$BASE/runner"
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
  curl -fsSL https://get.docker.com | sudo sh
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
  cat > "$BASE/.env" <<'EOF'
# Production secrets for SpeedStats. Fill these in once; docker compose passes them to the api and scraper.
SRC_PROXIES=<heroku-app-1>,<heroku-app-2>,...
SRC_PER_PROXY_CONCURRENCY=2
EXCLUDED_GAMES=w6jrzxdj
EXCLUDED_CATEGORIES=n2y350ed,5dw43j0k
EXCLUDED_PLAYERS=
MIN_LEADERBOARDS=600000
PUBLIC_URL=https://new.speedstats.app
# Cloudflare: zone id of speedstats.app and an API token with "Zone.Cache Purge" permission
CLOUDFLARE_ZONE_ID=
CLOUDFLARE_API_TOKEN=
# R2 (weekly snapshots): bucket + an API token with object read/write
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=speedstats
EOF
  chmod 600 "$BASE/.env"
  log "wrote $BASE/.env template - edit it (nano $BASE/.env) and fill in the proxies and tokens"
fi

# 3. GitHub Actions runner ---------------------------------------------------------------------------------------
if [ ! -f "$RUNNER_DIR/.runner" ]; then
  if [ -z "${RUNNER_TOKEN:-}" ]; then
    echo "RUNNER_TOKEN is required to register the runner (GitHub -> Settings -> Actions -> Runners -> New)" >&2
    exit 1
  fi
  log "installing GitHub Actions runner $RUNNER_VERSION ($RUNNER_ARCH)"
  mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz && rm runner.tar.gz
  ./config.sh --unattended --url "https://github.com/$GH_REPO" --token "$RUNNER_TOKEN" \
    --name "$(hostname)" --labels speedstats --work _work --replace
  sudo ./svc.sh install "$USER"
  sudo ./svc.sh start
  cd - >/dev/null
else
  log "runner already configured in $RUNNER_DIR"
fi

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
  1. edit $BASE/.env (proxies, Cloudflare + R2 tokens), then: cd $BASE && docker compose up -d api
  2. Cloudflare Zero Trust -> Tunnels -> your tunnel -> add public hostnames:
       new.speedstats.app  -> http://localhost:8000
       logs.speedstats.app -> http://localhost:9999  (+ an Access policy)
  3. GitHub -> Actions -> "Weekly scrape" -> Run workflow (untick resume) for the first crawl,
     or bootstrap from R2 once snapshots exist: docker compose run --rm scraper bootstrap
EOF
