#!/usr/bin/env bash
# Build SpaceAvoider native helper binaries.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build"

mkdir -p "${BUILD_DIR}"

if ! command -v g++ >/dev/null 2>&1; then
    printf '[build][error] missing g++; install build-essential\n' >&2
    exit 1
fi

if ! command -v sdl2-config >/dev/null 2>&1; then
    printf '[build][error] missing sdl2-config; install libsdl2-dev\n' >&2
    exit 1
fi

printf '[build] building native/audio_player.cpp\n'
g++ \
    -std=c++17 \
    -O2 \
    -Wall \
    -Wextra \
    "${PROJECT_ROOT}/native/audio_player.cpp" \
    -o "${BUILD_DIR}/audio_player" \
    $(sdl2-config --cflags --libs) \
    -lSDL2_mixer

printf '[build] wrote %s\n' "${BUILD_DIR}/audio_player"
