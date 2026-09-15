#!/usr/bin/env bash
# Install the pinned Freerouting jar and the JRE it needs under
# toolchain/freerouting/ (the ffmpeg posture: exact files, checked, never
# committed). Re-runnable; skips what is already there and correct.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${REPO_ROOT}/toolchain/freerouting"
FR_VERSION="2.4.1"
FR_JAR="freerouting-${FR_VERSION}.jar"
FR_URL="https://github.com/freerouting/freerouting/releases/download/v${FR_VERSION}/${FR_JAR}"
FR_SHA256="251101c3eeac22d7"   # first 16 hex of the sha256, checked below
JRE_MAJOR="25"                 # Freerouting 2.4.1 is class-file 69 = Java 25
mkdir -p "${DEST}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) JRE_OS="mac"; JRE_ARCH="aarch64" ;;
  Darwin-x86_64) JRE_OS="mac"; JRE_ARCH="x64" ;;
  Linux-aarch64) JRE_OS="linux"; JRE_ARCH="aarch64" ;;
  Linux-x86_64) JRE_OS="linux"; JRE_ARCH="x64" ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
JRE_URL="https://api.adoptium.net/v3/binary/latest/${JRE_MAJOR}/ga/${JRE_OS}/${JRE_ARCH}/jre/hotspot/normal/eclipse"

if [ ! -f "${DEST}/${FR_JAR}" ]; then
  echo "fetching ${FR_JAR}"
  curl -sL -o "${DEST}/${FR_JAR}.part" "${FR_URL}"
  mv "${DEST}/${FR_JAR}.part" "${DEST}/${FR_JAR}"
fi
have="$(shasum -a 256 "${DEST}/${FR_JAR}" | cut -c1-16)"
if [ "${have}" != "${FR_SHA256}" ]; then
  echo "${FR_JAR} sha256 prefix ${have} != pinned ${FR_SHA256}" >&2; exit 1
fi

if ! ls -d "${DEST}"/jdk-*/Contents/Home/bin/java "${DEST}"/jdk-*/bin/java >/dev/null 2>&1; then
  echo "fetching Temurin ${JRE_MAJOR} JRE (${JRE_OS}/${JRE_ARCH})"
  curl -sL -o "${DEST}/jre.tar.gz" "${JRE_URL}"
  tar -xzf "${DEST}/jre.tar.gz" -C "${DEST}"
  rm -f "${DEST}/jre.tar.gz"
fi
JAVA="$(ls -d "${DEST}"/jdk-*/Contents/Home/bin/java "${DEST}"/jdk-*/bin/java 2>/dev/null | head -1)"
"${JAVA}" -version 2>&1 | head -1
"${JAVA}" -Djava.awt.headless=true -jar "${DEST}/${FR_JAR}" -h 2>&1 | grep -m1 "Freerouting v" || true
echo "freerouting ready at ${DEST}"
