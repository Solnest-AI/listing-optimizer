#!/usr/bin/env bash
# Listing Optimizer - Mac/Linux setup. Run:  bash setup.sh
# Claude Code runs it as:                    bash setup.sh --no-prompt --auto-install
# Safe to rerun: keeps your existing .env keys, .venv, config, history and reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys/tests need attention.
set -u
cd "$(dirname "$0")" || exit 1

SKIP_TESTS=0; NO_PROMPT=0; AUTO_INSTALL=0
for a in "$@"; do
  case "$a" in
    --skip-tests) SKIP_TESTS=1 ;;
    --no-prompt) NO_PROMPT=1 ;;
    --auto-install) AUTO_INSTALL=1 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

PROBLEMS=()
step() { printf '\n[%s/6] %s\n' "$1" "$2"; }
ok()   { printf '  OK  %s\n' "$1"; }
warn() { printf '  !!  %s\n' "$1"; }
fail() { printf '\n  SETUP STOPPED: %s\n\n' "$1"; exit 1; }
ask()  { # yes/no; auto-install answers yes, no-prompt answers no
  [ "$AUTO_INSTALL" = 1 ] && return 0
  [ "$NO_PROMPT" = 1 ] && return 1
  read -r -p "$1 [Y/n] " r; [[ ! "$r" =~ ^[nN] ]]
}

# Newest first; prints the first interpreter that is 3.10 or newer.
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v=$("$c" -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>/dev/null) || continue
    set -- $v
    if [ "$1" = 3 ] && [ "$2" -ge 10 ]; then echo "$c"; return 0; fi
  done
  return 1
}

printf '\n  Listing Optimizer setup\n  Folder: %s\n' "$PWD"

# ---------------------------------------------------------------- 1. Python
step 1 'Python 3.10 or newer'
PY=$(find_python || true)
if [ -z "$PY" ]; then
  warn 'Python 3.10+ was not found.'
  if command -v brew >/dev/null 2>&1 && ask '  Install Python now with Homebrew?'; then
    brew install python
    PY=$(find_python || true)
  fi
  [ -n "$PY" ] || fail 'Install Python from https://www.python.org/downloads/ then run setup again.'
fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])')"

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

# ---------------------------------------------------------------- 3. venv
step 3 'Python environment and packages (first time takes a minute)'
if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
  warn '.venv is not a Mac/Linux environment (copied from Windows?). Rebuilding it.'
  rm -rf .venv
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv || fail 'Could not create .venv.'
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt \
  || fail 'Package install failed. Check your internet connection and run setup again.'
ok 'Packages installed in .venv'

# ---------------------------------------------------------------- 4. .env
step 4 'API keys (.env)'
if [ ! -f .env ]; then cp .env.example .env; ok 'Created .env from .env.example'
else ok 'Using your existing .env (nothing overwritten)'; fi

env_get() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env | head -n1 | tr -d '"'"'" ; }
env_set() { # replace the line if present, else append; python keeps it exact and BOM-free
  .venv/bin/python - "$1" "$2" <<'EOF'
import sys, pathlib
name, value = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); lines = p.read_text(encoding="utf-8-sig").splitlines()
out, found = [], False
for l in lines:
    if l.split("=")[0].strip() == name: out.append(f"{name}={value}"); found = True
    else: out.append(l)
if not found: out.append(f"{name}={value}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
EOF
}

ask_key() { # name, purpose, where
  have=$(env_get "$1")
  [ -z "$have" ] && [ "$1" = HOSPITABLE_TOKEN ] && have=$(env_get HOSPITABLE_API_KEY)
  if [ -n "$have" ]; then ok "$1 is set"; return; fi
  if [ "$NO_PROMPT" = 1 ]; then warn "$1 is empty ($2)"; return; fi
  printf '\n  %s - %s\n  Get it here: %s\n' "$1" "$2" "$3"
  read -r -p '  Paste it and press Enter: ' val
  val=$(printf '%s' "$val" | tr -d '"'"' ")
  if [ -n "$val" ]; then env_set "$1" "$val"; ok "$1 saved to .env"
  else warn "$1 skipped. You can add it to .env later and rerun setup."; fi
}
ask_key AIRROI_API_KEY 'competitor comps' 'https://www.airroi.com/api/developer/activate'
ask_key GEMINI_API_KEY 'photo scoring' 'https://aistudio.google.com/apikey'
ask_key HOSPITABLE_TOKEN 'your listings (Hospitable users only; press Enter to skip)' \
  'my.hospitable.com > Apps > API access > Platform token'

# ---------------------------------------------------------------- 5. Tests
step 5 'Self-test'
if [ "$SKIP_TESTS" = 1 ]; then warn 'Skipped (--skip-tests)'
else
  out=$(.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1); rc=$?
  if [ $rc -eq 0 ]; then ok "$(printf '%s\n' "$out" | tail -n1 | tr -d '=')"
  else printf '%s\n' "$out" | tail -n 25 | sed 's/^/    /'
       PROBLEMS+=('Some self-tests failed (output above). Send a screenshot to the Solnest team.'); fi
fi

# ---------------------------------------------------------------- 6. Keys
step 6 'Checking your keys (free, read-only)'
.venv/bin/python scripts/check_keys.py || PROBLEMS+=('Fix the keys marked !! in .env, then rerun setup.')

# ---------------------------------------------------------------- Done
echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then echo '  ALL SET.'
else echo '  ALMOST THERE. Fix these, then run setup again:'; for p in "${PROBLEMS[@]}"; do echo "   - $p"; done; fi
printf '\n  Next: open Claude Code in this folder and say:\n    "Optimize my <listing name> for <season>."\n'
printf "  Reports land on your Desktop in the 'Listing Optimizer' folder.\n\n"
[ ${#PROBLEMS[@]} -eq 0 ] && exit 0 || exit 2
