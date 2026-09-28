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
  curl -fsSL --retry 3 -o "${DEST}/${FR_JAR}.part" "${FR_URL}"
  mv "${DEST}/${FR_JAR}.part" "${DEST}/${FR_JAR}"
fi
have="$(shasum -a 256 "${DEST}/${FR_JAR}" | cut -c1-16)"
if [ "${have}" != "${FR_SHA256}" ]; then
  echo "${FR_JAR} sha256 prefix ${have} != pinned ${FR_SHA256}" >&2; exit 1
fi

# One glob matches per platform (mac: Contents/Home, linux: bin). Never `ls a b | head -1` here:
# under `set -eo pipefail` the unmatched glob makes ls exit 1, the pipeline fails, and the script
# dies (an assignment) or re-downloads a JRE it already has (a test) — both seen on every run.
find_java() {
  local cand
  for cand in "${DEST}"/jdk-*/Contents/Home/bin/java "${DEST}"/jdk-*/bin/java; do
    if [ -x "${cand}" ]; then echo "${cand}"; return 0; fi
  done
  return 1
}
if ! find_java >/dev/null; then
  echo "fetching Temurin ${JRE_MAJOR} JRE (${JRE_OS}/${JRE_ARCH})"
  curl -fsSL --retry 3 -o "${DEST}/jre.tar.gz" "${JRE_URL}"
  tar -xzf "${DEST}/jre.tar.gz" -C "${DEST}"
  rm -f "${DEST}/jre.tar.gz"
fi
JAVA="$(find_java)" || { echo "no java under ${DEST} after unpacking the JRE" >&2; exit 1; }
# Not `| head -1`: under `pipefail` head closing the pipe early gives java SIGPIPE (141) and the
# script exits 1 AFTER a successful install — which is what Harness saw on every setup run.
ver="$("${JAVA}" -version 2>&1)"; echo "${ver%%$'\n'*}"
"${JAVA}" -Djava.awt.headless=true -jar "${DEST}/${FR_JAR}" -h 2>&1 | grep -m1 "Freerouting v" || true
echo "freerouting ready at ${DEST}"
