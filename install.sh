#!/bin/bash
# RentMate Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/ahmedjafri/rentmate/main/install.sh | bash
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

REPO_URL="https://github.com/ahmedjafri/rentmate.git"
INSTALL_DIR="${RENTMATE_DIR:-$HOME/rentmate}"
MIN_NODE=18
MIN_PYTHON="3.12"

ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}$*${NC}"; }

# ── Prerequisites ─────────────────────────────────────────────────────────────

check_node() {
    if ! command -v node &>/dev/null; then
        die "Node.js not found. Install Node ≥${MIN_NODE} from https://nodejs.org and re-run."
    fi
    local ver
    ver=$(node -e "process.stdout.write(String(process.versions.node.split('.')[0]))")
    if [[ "$ver" -lt "$MIN_NODE" ]]; then
        die "Node.js v${ver} found but v${MIN_NODE}+ is required. Update at https://nodejs.org"
    fi
    ok "Node.js v$(node -v | tr -d v) (≥${MIN_NODE} required)"
}

check_python() {
    local py=""
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then py="$cmd"; break; fi
    done
    if [[ -z "$py" ]]; then
        die "Python not found. Install Python ≥${MIN_PYTHON} from https://python.org and re-run."
    fi
    local ver
    ver=$("$py" -c "import sys; print('%d.%d' % sys.version_info[:2])")
    # Compare versions
    local major minor req_major req_minor
    major="${ver%%.*}"; minor="${ver##*.}"
    req_major="${MIN_PYTHON%%.*}"; req_minor="${MIN_PYTHON##*.}"
    if [[ "$major" -lt "$req_major" ]] || { [[ "$major" -eq "$req_major" ]] && [[ "$minor" -lt "$req_minor" ]]; }; then
        die "Python ${ver} found but ${MIN_PYTHON}+ is required."
    fi
    ok "Python ${ver} (≥${MIN_PYTHON} required)"
}

check_poetry() {
    if ! command -v poetry &>/dev/null; then
        info "Poetry not found — installing..."
        curl -sSL https://install.python-poetry.org | python3 -
        # Add to PATH for this session
        export PATH="$HOME/.local/bin:$PATH"
        if ! command -v poetry &>/dev/null; then
            die "Poetry install failed. Install manually: https://python-poetry.org/docs/#installation"
        fi
    fi
    ok "Poetry $(poetry --version | awk '{print $NF}')"
}

check_git() {
    if ! command -v git &>/dev/null; then
        die "git not found. Install git and re-run."
    fi
    ok "git $(git --version | awk '{print $3}')"
}

# ── Clone ─────────────────────────────────────────────────────────────────────

clone_or_update() {
    if [[ "${NO_CLONE:-0}" == "1" ]]; then
        [[ -d "$INSTALL_DIR" ]] || die "NO_CLONE=1 but RENTMATE_DIR (${INSTALL_DIR}) does not exist."
        ok "Source at ${INSTALL_DIR} (pre-populated, skipping clone)"
        return
    fi
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Found existing install at ${INSTALL_DIR}, pulling latest..."
        git -C "$INSTALL_DIR" pull --ff-only
    elif [[ -d "$INSTALL_DIR" ]]; then
        die "${INSTALL_DIR} exists but is not a git repo. Remove it or set RENTMATE_DIR to a different path."
    else
        info "Cloning into ${INSTALL_DIR}..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
    ok "Source at ${INSTALL_DIR}"
}

# ── Install deps ──────────────────────────────────────────────────────────────

install_deps() {
    info "Installing dependencies (Python + frontend)..."
    # npm postinstall runs: poetry install && npm install --prefix www/rentmate
    npm install --prefix "$INSTALL_DIR"
    ok "Dependencies installed"
}

# ── Environment setup ─────────────────────────────────────────────────────────

setup_env() {
    local env_file="$INSTALL_DIR/.env"
    if [[ -f "$env_file" ]]; then
        info ".env already exists — skipping"
        return
    fi

    cp "$INSTALL_DIR/.env.example" "$env_file"

    local api_key=""
    if [[ -t 0 && -t 1 ]]; then
        echo ""
        echo -e "${BOLD}LLM API key${NC} ${DIM}(any OpenAI-compatible key; press Enter to skip and edit .env later)${NC}"
        read -rp "  API key: " api_key </dev/tty || true
    fi

    if [[ -n "$api_key" ]]; then
        # Works on both macOS (BSD sed) and Linux (GNU sed)
        sed -i.bak "s|^LLM_API_KEY=.*|LLM_API_KEY=${api_key}|" "$env_file" && rm -f "${env_file}.bak"
        ok ".env created with API key"
    else
        ok ".env created — set LLM_API_KEY before starting"
    fi
}

# ── Build frontend ────────────────────────────────────────────────────────────

build_frontend() {
    info "Building frontend..."
    npm run build --prefix "$INSTALL_DIR"
    ok "Frontend built"
}

# ── Done ──────────────────────────────────────────────────────────────────────

print_next_steps() {
    local env_file="$INSTALL_DIR/.env"
    local key_set=false
    if grep -q "^LLM_API_KEY=sk-" "$env_file" 2>/dev/null; then key_set=true; fi

    echo ""
    echo -e "${GREEN}${BOLD}RentMate installed!${NC}"
    echo ""

    if [[ "$key_set" == "false" ]]; then
        echo -e "  ${YELLOW}Before starting, set your API key:${NC}"
        echo -e "  ${DIM}${INSTALL_DIR}/.env${NC} → ${BOLD}LLM_API_KEY=sk-...${NC}"
        echo ""
    fi

    echo -e "  Start:   ${BOLD}cd ${INSTALL_DIR} && npm run dev${NC}"
    echo -e "  Open:    ${CYAN}http://localhost:8002${NC}  (password: rentmate)"
    echo -e "  Docs:    ${CYAN}https://github.com/ahmedjafri/rentmate${NC}"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    echo -e "${BOLD}RentMate Installer${NC}"
    echo -e "${DIM}https://github.com/ahmedjafri/rentmate${NC}"

    step "Checking prerequisites"
    check_node
    check_python
    check_poetry
    check_git

    step "Setting up RentMate"
    clone_or_update
    install_deps
    setup_env
    build_frontend

    print_next_steps
}

main "$@"
