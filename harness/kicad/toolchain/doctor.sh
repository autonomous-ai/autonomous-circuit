#!/usr/bin/env bash
# KiCad harness — Harness DSH doctor: can THIS machine author, check and show a KiCad-native board?
# cwd = the install dir. One line per check: `ok   <what>` / `warn <what>` / `miss <what>`.
# Exit 1 on any miss.
#
# Unlike Copper (the v1 harness), KiCad is REQUIRED here, not reported: the schematic, the PCB,
# ERC/DRC, the previews and the prototype packet all come out of kicad-cli and pcbnew. Without
# it there is no board at all, so a missing KiCad is a miss. Freerouting is vendored by setup.sh
# and a miss means "run setup"; a machine without it hand-routes, which is not a KiCad harness board.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
status=0
ok()   { echo "ok   $*"; }
warn() { echo "warn $*"; }
miss() { echo "miss $*"; status=1; }

# The engine. The manifest names one (harness.json in the install dir, the cwd); that CLI must be on
# the machine. Harness looks on PATH and, for grok, at the installer's default ~/.local/bin/grok.
engine="$(sed -n 's/^[[:space:]]*"engine":[[:space:]]*"\([a-z]*\)".*/\1/p' harness.json 2>/dev/null | head -1)"
case "${engine:-claude}" in
  claude)
    if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then ok "claude on PATH"
    else miss "claude not found — install Claude Code (https://claude.ai/install)"; fi ;;
  codex)
    if command -v codex >/dev/null 2>&1; then ok "codex on PATH"
    else miss "codex not found — npm i -g @openai/codex"; fi ;;
  grok)
    grok_bin="$(command -v grok 2>/dev/null || true)"
    [ -z "$grok_bin" ] && [ -x "$HOME/.local/bin/grok" ] && grok_bin="$HOME/.local/bin/grok"
    [ -z "$grok_bin" ] && [ -x "$HOME/.grok/bin/grok" ] && grok_bin="$HOME/.grok/bin/grok"
    if [ -n "$grok_bin" ]; then
      ok "grok $("$grok_bin" --version 2>/dev/null | head -1 | sed 's/^grok //') ($grok_bin)"
      # Grok Build signs in through a browser session (~/.grok/auth.json) or XAI_API_KEY. The pane's
      # shell is a login shell, so an export in the shell rc counts even when this check cannot see it.
      if [ -f "$HOME/.grok/auth.json" ] || [ -n "${XAI_API_KEY:-}" ] || [ -n "${OPENROUTER_API_KEY:-}" ]; then ok "grok auth (session or an API key in the environment)"
      else warn "no grok auth visible — run \`grok login\`, or export XAI_API_KEY / OPENROUTER_API_KEY in your shell rc"; fi
      # The tile's Grok settings (compaction under the 200k tier, no Cursor/Claude imports) live in the
      # user's ~/.grok/config.toml; setup writes them, grok-config.py --check reads them back.
      if [ -f "$HERE/grok-config.py" ]; then
        "$HERE/python" "$HERE/grok-config.py" --check || true
      fi
    else
      miss "grok not found — curl -fsSL https://x.ai/cli/install.sh | bash"
    fi ;;
  *)
    miss "engine \"$engine\" is not one this harness knows (claude, codex, grok)" ;;
esac

node_bin="$(command -v node || true)"
[ -z "$node_bin" ] && [ -x /opt/homebrew/opt/node@22/bin/node ] && node_bin=/opt/homebrew/opt/node@22/bin/node
if [ -n "$node_bin" ]; then
  v="$("$node_bin" --version 2>/dev/null | sed 's/^v//')"
  major="${v%%.*}"; rest="${v#*.}"; minor="${rest%%.*}"
  case "$major" in (*[!0-9]*|"") major=0; minor=0;; esac
  if [ "$major" -gt 22 ] || { [ "$major" -eq 22 ] && [ "${minor:-0}" -ge 12 ]; }; then
    ok "node $v"
  else
    miss "node $v is older than 22.12 (brew install node@22)"
  fi
else
  miss "node not found (brew install node@22)"
fi

# Host Python, through the same wrapper the agent uses, with kicadpy + circuitpy importable.
if pyv="$("$HERE/python" -c 'import sys, kicadpy, circuitpy; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"; then
  ok "python $pyv with kicadpy + circuitpy (toolchain/python)"
else
  miss "no python >= 3.10 with kicadpy importable (brew install python@3.12, or set CIRCUIT_PYTHON)"
fi

kicad=""
for cand in "${KICADPY_CLI:-}" "$(command -v kicad-cli 2>/dev/null || true)" \
            /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli \
            /Applications/KiCad.app/Contents/MacOS/kicad-cli \
            "$HOME/Applications/KiCad.app/Contents/MacOS/kicad-cli" \
            "$HOME/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then kicad="$cand"; break; fi
done
if [ -n "$kicad" ]; then
  ok "kicad-cli $("$kicad" version 2>/dev/null | head -1) ($kicad)"
else
  miss "kicad-cli not found — KiCad is required for a native board (brew install --cask kicad, or set KICADPY_CLI)"
fi

# KiCad's bundled Python with pcbnew: the authoring runtime. Resolved as kicadpy resolves it.
kpy="${KICADPY_PYTHON:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3}"
if [ -x "$kpy" ] && "$kpy" -c 'import pcbnew' >/dev/null 2>&1; then
  ok "pcbnew via KiCad's Python ($("$kpy" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
else
  miss "KiCad's bundled Python cannot import pcbnew (set KICADPY_PYTHON to a python3 that can)"
fi

fr="$ROOT/toolchain/freerouting"
java=""
for cand in "$fr"/jdk-*/Contents/Home/bin/java "$fr"/jdk-*/bin/java; do   # one glob per platform; never `ls a b`
  if [ -x "$cand" ]; then java="$cand"; break; fi
done
if [ -f "$fr/freerouting-2.4.1.jar" ] && [ -n "$java" ]; then
  ok "freerouting 2.4.1 + JRE vendored ($("$java" -version 2>&1 | head -1))"
else
  miss "freerouting not vendored at $fr — run harness/kicad/toolchain/setup.sh"
fi

# Firmware toolchains. RP2040 is vendored by setup.sh (the Firmware phase asks for a built binary,
# so a missing compiler is a miss); ESP32 firmware builds with the machine's arduino-cli + esp32 core,
# which the tile does not vendor — reported, not required.
pico="$ROOT/toolchain/pico"
if [ -x "$pico/arm-gnu-toolchain/bin/arm-none-eabi-gcc" ] && [ -f "$pico/pico-sdk/lib/tinyusb/src/tusb.h" ]; then
  gccv="$("$pico/arm-gnu-toolchain/bin/arm-none-eabi-gcc" --version 2>/dev/null)"; gccv="${gccv%%$'\n'*}"
  ok "rp2040 toolchain: ${gccv#arm-none-eabi-gcc } + pico-sdk (toolchain/pico)"
  if [ -f "$pico/picotool/picotool/picotoolConfig.cmake" ]; then ok "picotool vendored (toolchain/pico/picotool)"
  else warn "picotool not vendored — pico-sdk builds it per project from the network"; fi
  if [ -x "$pico/tools/bin/cmake" ] && [ -x "$pico/tools/bin/ninja" ]; then
    ok "cmake $("$pico/tools/bin/cmake" --version 2>/dev/null | head -1 | sed 's/cmake version //') + ninja vendored (toolchain/pico/tools)"
  elif command -v cmake >/dev/null 2>&1; then ok "cmake $(cmake --version 2>/dev/null | head -1 | sed 's/cmake version //') on PATH (not vendored — run setup.sh to vendor it)"
  else miss "cmake not found — RP2040 firmware cannot be built (run harness/kicad/toolchain/setup.sh, or brew install cmake ninja)"; fi
else
  miss "rp2040 toolchain not vendored at $pico — run harness/kicad/toolchain/setup.sh"
fi
if command -v arduino-cli >/dev/null 2>&1; then
  if arduino-cli core list 2>/dev/null | grep -q '^esp32:esp32'; then ok "arduino-cli with the esp32 core (ESP32 firmware builds)"
  else warn "arduino-cli without the esp32 core — ESP32 firmware will not build (arduino-cli core install esp32:esp32)"; fi
else
  warn "arduino-cli not found — ESP32 firmware will not build on this machine (brew install arduino-cli)"
fi

if [ -f "$ROOT/viewer/dist/index.html" ]; then
  ok "viewer built"
else
  miss "viewer not built — run harness/kicad/toolchain/setup.sh"
fi

exit $status
