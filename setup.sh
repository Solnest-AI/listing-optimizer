#!/usr/bin/env bash
# Listing Optimizer - Mac/Linux setup. Claude Code runs it for you:
#   bash setup.sh --no-prompt --auto-install
# Safe to rerun: keeps your .env keys, .venv, config, history and reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys or tests need attention.
#
# Built to match the STR Secrets Connections kit: Python comes from uv (the kit installs it
# too), and keys never touch the chat. They are copied from the kit by scripts/kit_link.py;
# anything blank or rejected is pasted by the attendee into the kit's .env (the one place keys
# live), which this script opens, and the next run copies it over. No kit: this folder's .env.
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
# --no-project --system: the real interpreter (uv-managed or not), never this folder's .venv.
find_uv_python() { UV_PYTHON_DOWNLOADS=never "$1" python find --no-project --system "$PYV" 2>/dev/null; }
venv_ok() { [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys' >/dev/null 2>&1; }

printf '\n  Listing Optimizer setup\n  Folder: %s\n' "$PWD"

# ---------------------------------------------------------------- 1. uv + Python
step 1 "Python $PYV through uv"
UV=$(find_uv || true)
if [ -z "$UV" ] && ask '  Install uv now (Astral installer, no admin prompt)?'; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  UV=$(find_uv || true)
fi
[ -n "$UV" ] || fail 'uv could not be installed. Check your internet connection and run setup again, or install it yourself from https://docs.astral.sh/uv/getting-started/installation/ and rerun.'
ok "uv $("$UV" --version 2>/dev/null | awk '{print $2}')"
PYEXE=$(find_uv_python "$UV" || true)
if [ -z "$PYEXE" ] && ask "  Install Python $PYV through uv now?"; then
  "$UV" python install "$PYV" >/dev/null 2>&1
  PYEXE=$(find_uv_python "$UV" || true)
fi
[ -n "$PYEXE" ] || fail "uv could not provide Python $PYV. Check your internet connection and run setup again."
ok "Python $PYV (uv): $PYEXE"

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
if [ -d .venv ] && ! venv_ok; then
  warn '.venv does not run here (copied from another machine or Windows?). Rebuilding it.'
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  "$UV" venv .venv --python "$PYEXE" --quiet && venv_ok || fail 'Could not create .venv.'
fi
"$UV" pip install --python .venv/bin/python --quiet -r requirements.txt -r requirements-dev.txt \
  || fail 'Package install failed. Check your internet connection and run setup again.'
ok "Packages installed in .venv (Python $(.venv/bin/python -c 'import sys; print(sys.version.split()[0])'))"

# ---------------------------------------------------------------- 4. keys
step 4 'API keys: copied from your STR Secrets Connections kit'
if [ ! -f .env ]; then cp .env.example .env; ok 'Created .env from .env.example'
else ok 'Using your existing .env (nothing overwritten)'; fi
if [ -n "$KIT" ]; then kit_out=$(.venv/bin/python scripts/kit_link.py --kit "$KIT"); else kit_out=$(.venv/bin/python scripts/kit_link.py); fi
rc=$?
printf '%s\n' "$kit_out"
KITDIR=""
while IFS= read -r p; do [ -f "$p/fan-out-env.sh" ] && { KITDIR="$p"; break; }; done < <(printf '%s\n' "$kit_out" | sed -n 's/^\[kit\] \(.*\)$/\1/p')
[ $rc -eq 1 ] && warn "No kit found: standalone install. Keys live in this folder's .env only (github.com/Solnest-AI/str-secrets-connections has the kit)."
# Where a missing or rejected key gets pasted. With a kit linked, the kit's .env is the one
# place keys live and the next run copies it over; a value pasted here would be overwritten.
ENV_TO_OPEN="$PWD/.env"; ENV_LABEL="this folder's .env"
[ -n "$KITDIR" ] && { ENV_TO_OPEN="$KITDIR/.env"; ENV_LABEL="the kit's .env ($ENV_TO_OPEN)"; }
NEED_ENV=0; [ $rc -eq 2 ] && NEED_ENV=1

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
if ! .venv/bin/python scripts/check_keys.py; then
  PROBLEMS+=("Paste each key marked !! into $ENV_LABEL (never into the chat), save, then rerun setup or say \"saved\".")
  NEED_ENV=1
fi
if [ "$NEED_ENV" = 1 ] && [ -z "${LO_NO_OPEN:-}" ]; then
  # Keys go into the file, never into the chat and never through a prompt.
  echo "  Opening $ENV_LABEL: paste each key marked !! after its = sign, save, then say \"saved\"."
  case "$(uname)" in Darwin) open -e "$ENV_TO_OPEN" 2>/dev/null || open "$ENV_TO_OPEN" 2>/dev/null ;; *) xdg-open "$ENV_TO_OPEN" >/dev/null 2>&1 ;; esac \
    || warn "Could not open an editor. Open this file yourself: $ENV_TO_OPEN"
fi

# ---------------------------------------------------------------- done
echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then echo '  ALL SET.'
else echo '  ALMOST THERE. Fix these, then run setup again:'; for p in "${PROBLEMS[@]}"; do echo "   - $p"; done; fi
printf '\n  Next: in Claude Code, in this folder, say:\n    "Optimize my <listing name> for <season>."\n'
printf "  Reports land on your Desktop in the 'Listing Optimizer' folder.\n\n"
[ ${#PROBLEMS[@]} -eq 0 ] && exit 0 || exit 2
