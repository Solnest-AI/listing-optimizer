#!/usr/bin/env bash
# Listing Optimizer - Mac/Linux setup. Claude Code runs it for you:
#   bash setup.sh --no-prompt --auto-install
# Safe to rerun: keeps your .env keys, .venv, config, history and reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys or tests need attention.
#
# Built to match the STR Secrets Connections kit: Python comes from uv (the kit installs it
# too), and keys never touch the chat: they are copied from the kit by scripts/kit_link.py and
# anything still blank is pasted by the attendee into .env, which this script opens for them.
set -u
cd "$(dirname "$0")" || exit 1

SKIP_TESTS=0; NO_PROMPT=0; AUTO_INSTALL=0; KIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-tests) SKIP_TESTS=1 ;;
    --no-prompt) NO_PROMPT=1 ;;
    --auto-install) AUTO_INSTALL=1 ;;
    --kit) KIT="$2"; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
  shift
done
PYV=3.13
PROBLEMS=()
step() { printf '\n[%s/6] %s\n' "$1" "$2"; }
ok()   { printf '  OK  %s\n' "$1"; }
warn() { printf '  !!  %s\n' "$1"; }
fail() { printf '\n  SETUP STOPPED: %s\n\n' "$1"; exit 1; }
ask()  { # yes/no; --auto-install answers yes, --no-prompt answers no
  [ "$AUTO_INSTALL" = 1 ] && return 0
  [ "$NO_PROMPT" = 1 ] && return 1
  read -r -p "$1 [Y/n] " r; [[ ! "$r" =~ ^[nN] ]]
}
find_uv() { for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv"; do [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }; done; return 1; }
find_system_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v=$("$c" -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>/dev/null) || continue
    set -- $v
    if [ "$1" = 3 ] && [ "$2" -ge 10 ]; then echo "$c"; return 0; fi
  done
  return 1
}

printf '\n  Listing Optimizer setup\n  Folder: %s\n' "$PWD"

# ---------------------------------------------------------------- 1. uv + Python
step 1 "Python $PYV through uv"
UV=$(find_uv || true)
if [ -z "$UV" ] && ask '  Install uv now (Astral installer, no admin prompt)?'; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  UV=$(find_uv || true)
fi
PYEXE=""; SYSPY=""
if [ -n "$UV" ]; then
  ok "uv $("$UV" --version 2>/dev/null | awk '{print $2}')"
  PYEXE=$(UV_PYTHON_DOWNLOADS=never "$UV" python find "$PYV" 2>/dev/null || true)
  if [ -z "$PYEXE" ] && ask "  Install Python $PYV through uv now?"; then
    "$UV" python install "$PYV" >/dev/null 2>&1
    PYEXE=$(UV_PYTHON_DOWNLOADS=never "$UV" python find "$PYV" 2>/dev/null || true)
  fi
  [ -n "$PYEXE" ] && ok "Python $PYV (uv): $PYEXE" || warn "uv could not provide Python $PYV."
else
  warn 'uv is not installed and could not be installed.'
fi
if [ -z "$PYEXE" ]; then
  SYSPY=$(find_system_python || true)
  [ -n "$SYSPY" ] || fail "No Python. Check your internet connection and run setup again (it installs uv and Python $PYV itself), or install Python from https://www.python.org/downloads/ and rerun."
  ok "falling back to system Python $("$SYSPY" -c 'import sys; print(sys.version.split()[0])')"
fi

# ---------------------------------------------------------------- 2. Git
step 2 'Git'
if command -v git >/dev/null 2>&1; then
  ok "$(git --version | sed 's/^git version /Git /')"
else
  warn 'Git is not installed.'
  if [ "$(uname)" = Darwin ] && ask '  Install the Apple command line tools (includes Git)?'; then
    xcode-select --install 2>/dev/null || true
    PROBLEMS+=('Finish the command line tools installer, then run setup again.')
  else
    PROBLEMS+=('Install Git from https://git-scm.com/downloads then run setup again.')
  fi
fi

# ---------------------------------------------------------------- 3. venv + packages
step 3 'Python environment and packages (first time takes a minute)'
if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
  warn '.venv is not a Mac/Linux environment (copied from Windows?). Rebuilding it.'
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  if [ -n "$UV" ] && [ -n "$PYEXE" ]; then "$UV" venv .venv --python "$PYEXE" --quiet || fail 'Could not create .venv.'
  else "$SYSPY" -m venv .venv || fail 'Could not create .venv.'; fi
fi
if [ -n "$UV" ]; then "$UV" pip install --python .venv/bin/python --quiet -r requirements.txt -r requirements-dev.txt
else .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt; fi \
  || fail 'Package install failed. Check your internet connection and run setup again.'
ok "Packages installed in .venv (Python $(.venv/bin/python -c 'import sys; print(sys.version.split()[0])'))"

# ---------------------------------------------------------------- 4. keys
step 4 'API keys: copied from your STR Secrets Connections kit'
if [ ! -f .env ]; then cp .env.example .env; ok 'Created .env from .env.example'
else ok 'Using your existing .env (nothing overwritten)'; fi
if [ -n "$KIT" ]; then .venv/bin/python scripts/kit_link.py --kit "$KIT"; else .venv/bin/python scripts/kit_link.py; fi
rc=$?
[ $rc -eq 1 ] && warn 'No kit found. Set it up first (github.com/Solnest-AI/str-secrets-connections), or paste keys into .env by hand.'
if [ $rc -ne 0 ] && [ -z "${LO_NO_OPEN:-}" ]; then
  # Keys go into the file, never into the chat and never through a prompt.
  echo '  Opening .env: paste each missing key after its = sign, save, then say "saved".'
  case "$(uname)" in Darwin) open -e .env ;; *) xdg-open .env >/dev/null 2>&1 || true ;; esac
fi

# ---------------------------------------------------------------- 5. tests
step 5 'Self-test'
if [ "$SKIP_TESTS" = 1 ]; then warn 'Skipped (--skip-tests)'
else
  out=$(.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1); trc=$?
  if [ $trc -eq 0 ]; then ok "$(printf '%s\n' "$out" | tail -n1 | tr -d '=')"
  else printf '%s\n' "$out" | tail -n 25 | sed 's/^/    /'
       PROBLEMS+=('Some self-tests failed (output above). Send a screenshot to the Solnest team.'); fi
fi

# ---------------------------------------------------------------- 6. key check
step 6 'Checking your keys (free, read-only)'
.venv/bin/python scripts/check_keys.py || PROBLEMS+=('Paste the keys marked !! into .env (never into the chat), save, then rerun setup or say "saved".')

# ---------------------------------------------------------------- done
echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then echo '  ALL SET.'
else echo '  ALMOST THERE. Fix these, then run setup again:'; for p in "${PROBLEMS[@]}"; do echo "   - $p"; done; fi
printf '\n  Next: in Claude Code, in this folder, say:\n    "Optimize my <listing name> for <season>."\n'
printf "  Reports land on your Desktop in the 'Listing Optimizer' folder.\n\n"
[ ${#PROBLEMS[@]} -eq 0 ] && exit 0 || exit 2
