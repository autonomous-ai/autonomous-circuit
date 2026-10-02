#!/usr/bin/env bash
# Install the RP2040 firmware toolchain under toolchain/pico/ (the ffmpeg posture: exact
# versions, checked where we have a checksum, never committed). Re-runnable; skips what is
# already there.
#
#   toolchain/pico/arm-gnu-toolchain/   Arm GNU 14.2.Rel1 (arm-none-eabi-gcc)   → PICO_TOOLCHAIN_PATH
#   toolchain/pico/pico-sdk/            pico-sdk 2.2.0 + lib/tinyusb              → PICO_SDK_PATH
#   toolchain/pico/picotool/picotool/   picotool 2.2.0, built here, no libusb    → CMAKE_PREFIX_PATH=toolchain/pico/picotool
#   toolchain/pico/tools/bin/           cmake + ninja from PyPI wheels           → KICAD_HARNESS_CMAKE / _NINJA
#
# Why vendor a gigabyte: the KiCad tile's firmware phase is "done" only when the recipe's binary
# exists (kicadpy.firmware.built). On 2026-09-30 Opus 5.5 wrote RP2040 firmware it could not build
# — no arm-none-eabi-gcc on the machine — and the same sources failed to compile once Astra had
# downloaded exactly these three things into its workspace. A tile that asks for a build carries
# the compiler.
#
# PICO_TOOLCHAIN_SEED=<dir> copies from a directory that already holds `arm-gnu.tar.xz`,
# `pico-sdk/` and `picotool-install/` (an earlier download) instead of fetching.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${REPO_ROOT}/toolchain/pico"
ARM_REL="14.2.rel1"
SDK_TAG="2.2.0"
PICOTOOL_TAG="2.2.0"
SEED="${PICO_TOOLCHAIN_SEED:-}"
mkdir -p "${DEST}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ARM_HOST="darwin-arm64";  ARM_SHA256="c7c78ffab9bebfce" ;;   # first 16 hex, measured 2026-09-30
  Darwin-x86_64) ARM_HOST="darwin-x86_64"; ARM_SHA256="" ;;
  Linux-aarch64) ARM_HOST="aarch64";       ARM_SHA256="" ;;
  Linux-x86_64)  ARM_HOST="x86_64";        ARM_SHA256="" ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
ARM_NAME="arm-gnu-toolchain-${ARM_REL}-${ARM_HOST}-arm-none-eabi"
ARM_URL="https://developer.arm.com/-/media/Files/downloads/gnu/${ARM_REL}/binrel/${ARM_NAME}.tar.xz"
ARM_DIR="${DEST}/arm-gnu-toolchain"

# 1. Arm GNU. One directory, renamed from the archive's, so PICO_TOOLCHAIN_PATH never carries a version.
if [ ! -x "${ARM_DIR}/bin/arm-none-eabi-gcc" ]; then
  tarball="${DEST}/arm-gnu.tar.xz"
  if [ -n "${SEED}" ] && [ -d "${SEED}/${ARM_NAME}" ]; then
    echo "copying ${ARM_NAME} from ${SEED}"
    rm -rf "${ARM_DIR}"
    cp -R "${SEED}/${ARM_NAME}" "${ARM_DIR}"
  else
    if [ -n "${SEED}" ] && [ -f "${SEED}/arm-gnu.tar.xz" ]; then
      cp "${SEED}/arm-gnu.tar.xz" "${tarball}"
    elif [ ! -f "${tarball}" ]; then
      echo "fetching ${ARM_NAME}.tar.xz (~150 MB)"
      curl -fsSL --retry 3 -o "${tarball}.part" "${ARM_URL}"
      mv "${tarball}.part" "${tarball}"
    fi
    if [ -n "${ARM_SHA256}" ]; then
      have="$(shasum -a 256 "${tarball}" | cut -c1-16)"
      if [ "${have}" != "${ARM_SHA256}" ]; then
        echo "arm-gnu.tar.xz sha256 prefix ${have} != pinned ${ARM_SHA256}" >&2; exit 1
      fi
    else
      echo "no pinned checksum for ${ARM_HOST}; installing unverified (pin it in $(basename "$0") once measured)"
    fi
    rm -rf "${DEST}/${ARM_NAME}" "${ARM_DIR}"
    tar -xJf "${tarball}" -C "${DEST}"
    mv "${DEST}/${ARM_NAME}" "${ARM_DIR}"
    rm -f "${tarball}"
  fi
fi
[ -x "${ARM_DIR}/bin/arm-none-eabi-gcc" ] || { echo "no arm-none-eabi-gcc under ${ARM_DIR}" >&2; exit 1; }

# 2. pico-sdk at the tag, shallow, with the one submodule USB CDC firmware needs.
SDK_DIR="${DEST}/pico-sdk"
if [ ! -f "${SDK_DIR}/pico_sdk_init.cmake" ]; then
  if [ -n "${SEED}" ] && [ -f "${SEED}/pico-sdk/pico_sdk_init.cmake" ]; then
    echo "copying pico-sdk from ${SEED}"
    rm -rf "${SDK_DIR}"
    cp -R "${SEED}/pico-sdk" "${SDK_DIR}"
  else
    command -v git >/dev/null 2>&1 || { echo "git is required to fetch pico-sdk" >&2; exit 1; }
    echo "fetching pico-sdk ${SDK_TAG}"
    rm -rf "${SDK_DIR}"
    git clone -q --depth 1 -b "${SDK_TAG}" https://github.com/raspberrypi/pico-sdk.git "${SDK_DIR}"
    (cd "${SDK_DIR}" && git submodule update -q --init --depth 1 lib/tinyusb)
  fi
fi
[ -f "${SDK_DIR}/lib/tinyusb/src/tusb.h" ] || { echo "pico-sdk at ${SDK_DIR} has no lib/tinyusb — USB CDC firmware will not build" >&2; exit 1; }

# 2b. cmake + ninja. A blank Mac has neither, and a doctor that says "miss cmake" to a Store user is
# a tile that is not ready. PyPI ships both as wheels with the binaries inside (cmake 54 MB, ninja
# 0.3 MB), so a venv on the pipeline's own Python is the whole install. PICO_TOOLS_PYTHON picks the
# interpreter (the Store wrapper hands in its venv's); default: the newest python3 >= 3.10 on PATH.
TOOLS="${DEST}/tools"
if [ ! -x "${TOOLS}/bin/cmake" ] || [ ! -x "${TOOLS}/bin/ninja" ]; then
  tools_py="${PICO_TOOLS_PYTHON:-}"
  if [ -z "${tools_py}" ]; then
    for cand in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        tools_py="$(command -v "${cand}")"; break
      fi
    done
  fi
  if [ -n "${tools_py}" ]; then
    echo "installing cmake + ninja wheels into ${TOOLS}"
    rm -rf "${TOOLS}"
    "${tools_py}" -m venv "${TOOLS}"
    "${TOOLS}/bin/python" -m pip install -q --disable-pip-version-check cmake ninja
  else
    echo "no python3 >= 3.10 found: cmake + ninja not vendored (RP2040 firmware needs cmake on PATH)"
  fi
fi
if [ -x "${TOOLS}/bin/cmake" ]; then
  export PATH="${TOOLS}/bin:${PATH}"
fi

# 3. picotool, built once here (the SDK otherwise builds it per project, from the network).
PT_PREFIX="${DEST}/picotool"
if [ ! -f "${PT_PREFIX}/picotool/picotoolConfig.cmake" ]; then
  if [ -n "${SEED}" ] && [ -f "${SEED}/picotool-install/picotool/picotoolConfig.cmake" ]; then
    echo "copying picotool from ${SEED}"
    rm -rf "${PT_PREFIX}"
    cp -R "${SEED}/picotool-install" "${PT_PREFIX}"
  elif command -v cmake >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
    echo "building picotool ${PICOTOOL_TAG}"
    src="${DEST}/picotool-src"; build="${DEST}/picotool-build"
    rm -rf "${src}" "${build}"
    git clone -q --depth 1 -b "${PICOTOOL_TAG}" https://github.com/raspberrypi/picotool.git "${src}"
    gen=""; command -v ninja >/dev/null 2>&1 && gen="-G Ninja"
    # shellcheck disable=SC2086
    cmake -S "${src}" -B "${build}" ${gen} -DPICO_SDK_PATH="${SDK_DIR}" -DPICOTOOL_NO_LIBUSB=1 \
      -DCMAKE_INSTALL_PREFIX="${PT_PREFIX}" >/dev/null
    cmake --build "${build}" >/dev/null
    cmake --install "${build}" >/dev/null
    rm -rf "${src}" "${build}"
  else
    echo "cmake or git missing: picotool not built (pico-sdk will fetch it per project, which needs the network)"
  fi
fi

ver="$("${ARM_DIR}/bin/arm-none-eabi-gcc" --version 2>/dev/null)"; echo "${ver%%$'\n'*}"
echo "pico-sdk ${SDK_TAG} at ${SDK_DIR}"
[ -x "${TOOLS}/bin/cmake" ] && echo "cmake $("${TOOLS}/bin/cmake" --version | head -1 | sed 's/cmake version //') + ninja $("${TOOLS}/bin/ninja" --version) at ${TOOLS}/bin"
[ -f "${PT_PREFIX}/picotool/picotoolConfig.cmake" ] && echo "picotool at ${PT_PREFIX}/picotool"
echo "pico toolchain ready at ${DEST}"
