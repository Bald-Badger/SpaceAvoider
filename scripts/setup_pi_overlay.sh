#!/usr/bin/env bash
# One-time setup for SpaceAvoider on Stratux/Raspberry Pi.
#
# IMPORTANT:
# Run this only after disabling the Stratux overlay and rebooting:
#
#   sudo overlayctl disable
#   sudo reboot
#
# Then run:
#
#   cd /rwbase/playground/SpaceAvoider
#   sudo bash scripts/setup_pi_overlay.sh
#
# When setup is complete, re-enable overlay protection manually:
#
#   sudo overlayctl enable
#   sudo reboot

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PROJECT_VENV="${PROJECT_ROOT}/.venv"
SERVICE_TEMPLATE="${SCRIPT_DIR}/spaceavoider.service.in"
SERVICE_NAME="spaceavoider.service"
SERVICE_DESTINATION="/etc/systemd/system/${SERVICE_NAME}"
ARGON_ONE_INSTALLER_URL="https://download.argon40.com/argon1.sh"
APT_DATE_REFERENCE_URL="http://deb.debian.org/debian/"
MAX_CLOCK_SKEW_SECONDS=300

PROJECT_PIP_PACKAGES=(
    adafruit-circuitpython-dht
    adafruit-circuitpython-matrixkeypad
    sparkfun-qwiic-bmp581
)

SYSTEM_APT_PACKAGES=(
    build-essential
    libgpiod2
    python3-dev
    python3-full
    python3-pygame
)


log() {
    printf '[setup] %s\n' "$*"
}


die() {
    printf '[setup][error] %s\n' "$*" >&2
    exit 1
}


run() {
    log "running: $*"
    "$@"
}


require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        die "Run with sudo: sudo bash scripts/setup_pi_overlay.sh"
    fi
}


require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}


root_filesystem_type() {
    findmnt -n -o FSTYPE / 2>/dev/null || printf unknown
}


require_overlay_disabled() {
    local root_type

    root_type="$(root_filesystem_type)"
    log "root filesystem type: ${root_type}"

    if [[ "${root_type}" == "overlay" ]]; then
        die "Overlay is still enabled. Run 'sudo overlayctl disable', reboot, then run this script again."
    fi
}


remote_http_date_epoch() {
    local date_text=""
    local line

    while IFS= read -r line; do
        line="${line%$'\r'}"
        case "${line,,}" in
            date:*)
                date_text="${line#*: }"
                ;;
        esac
    done < <(curl -fsSI --max-time 10 "${APT_DATE_REFERENCE_URL}" 2>/dev/null)

    [[ -n "${date_text}" ]] || return 1
    date -u -d "${date_text}" +%s
}


ensure_clock_for_apt() {
    local current_epoch
    local remote_epoch
    local skew_seconds
    local abs_skew_seconds

    log "checking system clock before apt operations"

    if ! remote_epoch="$(remote_http_date_epoch)"; then
        log "could not read Date header from ${APT_DATE_REFERENCE_URL}; continuing without clock correction"
        return 0
    fi

    current_epoch="$(date -u +%s)"
    skew_seconds=$((remote_epoch - current_epoch))
    abs_skew_seconds="${skew_seconds#-}"

    if ((abs_skew_seconds <= MAX_CLOCK_SKEW_SECONDS)); then
        log "system clock is close enough for apt"
        return 0
    fi

    log "system clock differs from ${APT_DATE_REFERENCE_URL} by ${skew_seconds} seconds"
    run date -u -s "@${remote_epoch}"
}


update_upgrade_and_clean_apt() {
    log "updating and upgrading apt packages"
    export DEBIAN_FRONTEND=noninteractive
    run apt-get update
    run apt-get upgrade -y
    run apt-get autoremove -y
    run apt-get clean
}


install_argon_one_driver() {
    log "installing Argon ONE driver"
    log "source: ${ARGON_ONE_INSTALLER_URL}"
    curl -fsSL "${ARGON_ONE_INSTALLER_URL}" | bash
}


install_python3_full() {
    log "installing Python system packages"
    export DEBIAN_FRONTEND=noninteractive
    run apt-get install -y "${SYSTEM_APT_PACKAGES[@]}"
}


project_owner() {
    stat -c '%U' "${PROJECT_ROOT}"
}


project_group() {
    stat -c '%G' "${PROJECT_ROOT}"
}


run_as_project_owner() {
    local owner

    owner="$(project_owner)"

    if [[ "${owner}" != "root" && "${owner}" != "UNKNOWN" ]] && command -v runuser >/dev/null 2>&1; then
        runuser -u "${owner}" -- "$@"
    elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]] && command -v sudo >/dev/null 2>&1; then
        sudo -H -u "${SUDO_USER}" "$@"
    else
        "$@"
    fi
}


install_runtime_service() {
    local owner
    local group
    local rendered_service

    owner="$(project_owner)"
    group="$(project_group)"

    if [[ "${owner}" == "root" || "${owner}" == "UNKNOWN" ]]; then
        if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
            owner="${SUDO_USER}"
            group="$(id -gn "${SUDO_USER}")"
        else
            die "Could not determine non-root project owner for ${SERVICE_NAME}"
        fi
    fi

    [[ -f "${SERVICE_TEMPLATE}" ]] || die "Missing service template: ${SERVICE_TEMPLATE}"
    require_command systemctl

    log "installing systemd service ${SERVICE_NAME} as ${owner}:${group}"
    rendered_service="$(mktemp)"
    sed \
        -e "s#__PROJECT_ROOT__#${PROJECT_ROOT}#g" \
        -e "s#__PROJECT_USER__#${owner}#g" \
        -e "s#__PROJECT_GROUP__#${group}#g" \
        "${SERVICE_TEMPLATE}" > "${rendered_service}"

    run install -m 0644 "${rendered_service}" "${SERVICE_DESTINATION}"
    rm -f "${rendered_service}"

    run systemctl daemon-reload
    run systemctl enable "${SERVICE_NAME}"
    log "service enabled for boot: ${SERVICE_NAME}"
    log "start now with: sudo systemctl start ${SERVICE_NAME}"
    log "view logs with: journalctl -u ${SERVICE_NAME} -f"
}


setup_python_venv() {
    log "creating/updating project Python virtual environment at ${PROJECT_VENV}"
    run_as_project_owner python3 -m venv --system-site-packages "${PROJECT_VENV}"
    run_as_project_owner "${PROJECT_VENV}/bin/python" -m pip install --upgrade pip
    run_as_project_owner "${PROJECT_VENV}/bin/python" -m pip uninstall -y pygame-ce pygame || true
    run_as_project_owner "${PROJECT_VENV}/bin/python" -m pip install "${PROJECT_PIP_PACKAGES[@]}"
    log "activate with: source ${PROJECT_VENV}/bin/activate"
}


main() {
    require_root
    require_command apt-get
    require_command bash
    require_command curl
    require_command date
    require_command findmnt
    require_command stat

    require_overlay_disabled
    ensure_clock_for_apt
    update_upgrade_and_clean_apt
    # install_argon_one_driver
    install_python3_full
    setup_python_venv
    install_runtime_service

    log "setup complete"
    log "Next: sudo overlayctl enable && sudo reboot"
}


main "$@"
