#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# INOT Mark1 — Raspberry Pi Setup Script (idempotent)
#
# Run once after cloning the repo on the Pi:
#   chmod +x scripts/setup_pi.sh && ./scripts/setup_pi.sh
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
VOSK_MODEL="vosk-model-small-en-us-0.15"
VOSK_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL}.zip"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"

echo "════════════════════════════════════════════════"
echo "  INOT Mark1 — Raspberry Pi Setup"
echo "════════════════════════════════════════════════"

# ── 1. System packages ───────────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git python3-venv python3-dev \
    portaudio19-dev libasound2-dev \
    libatlas-base-dev libopenblas-dev \
    curl wget unzip jq

# ── 2. Docker ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[2/7] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "  ⚠ You may need to log out and back in for docker group to take effect."
else
    echo "[2/7] Docker already installed."
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null 2>&1; then
    echo "  Installing Docker Compose plugin..."
    sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
fi

# ── 3. Python venv ────────────────────────────────────────────────────
echo "[3/7] Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel -q
pip install -r "$REPO_DIR/requirements.txt" -q

# ── 4. .env ───────────────────────────────────────────────────────────
echo "[4/7] Checking .env..."
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    # Auto-set Pi defaults
    sed -i 's/^PLATFORM=.*/PLATFORM=rpi/' "$REPO_DIR/.env"
    sed -i 's/^PLATFORM_LABEL=.*/PLATFORM_LABEL=rpi/' "$REPO_DIR/.env"
    sed -i 's/^STT_BACKEND=.*/STT_BACKEND=vosk/' "$REPO_DIR/.env"
    sed -i 's/^LLM_BACKEND=.*/LLM_BACKEND=ollama/' "$REPO_DIR/.env"
    sed -i 's/^CAMERA_ENABLED=.*/CAMERA_ENABLED=false/' "$REPO_DIR/.env"
    sed -i 's/^TTS_ENABLED=.*/TTS_ENABLED=false/' "$REPO_DIR/.env"
    sed -i 's/^FORCE_ONBOARDING=.*/FORCE_ONBOARDING=false/' "$REPO_DIR/.env"
    echo "  Created .env with Pi defaults. Edit GROQ_API_KEY if using cloud fallback."
else
    echo "  .env already exists."
fi

# ── 5. Docker services ───────────────────────────────────────────────
echo "[5/7] Starting Docker services (Mosquitto + Ollama)..."
cd "$REPO_DIR"
docker compose up -d

# ── 6. Pull Ollama model ─────────────────────────────────────────────
echo "[6/7] Pulling Ollama model: $OLLAMA_MODEL ..."
# Wait for Ollama to be ready
for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        break
    fi
    echo "  Waiting for Ollama to start... ($i/30)"
    sleep 2
done
docker compose exec -T ollama ollama pull "$OLLAMA_MODEL" || \
    echo "  ⚠ Could not pull model. Run manually: docker compose exec ollama ollama pull $OLLAMA_MODEL"

# ── 7. Vosk model ────────────────────────────────────────────────────
echo "[7/7] Checking Vosk model..."
if [ ! -d "$REPO_DIR/$VOSK_MODEL" ]; then
    echo "  Downloading $VOSK_MODEL..."
    wget -q "$VOSK_URL" -O "/tmp/${VOSK_MODEL}.zip"
    unzip -q "/tmp/${VOSK_MODEL}.zip" -d "$REPO_DIR"
    rm -f "/tmp/${VOSK_MODEL}.zip"
    echo "  Vosk model extracted to $REPO_DIR/$VOSK_MODEL"
else
    echo "  Vosk model already present."
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Copy room_context.json to this directory"
echo "    2. Run:  python scripts/preflight_pi.py"
echo "    3. Test: python pipeline.py --wav tests/fixtures/test_command.wav"
echo "    4. Live: python pipeline.py --loop"
echo "════════════════════════════════════════════════"

