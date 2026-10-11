#!/usr/bin/env bash
# One-shot deploy for AI Pulse. Requires `gh auth login` (done once by Metin).
# Usage: ./deploy.sh [repo-name]   (default: ai-pulse)
set -euo pipefail
cd "$(dirname "$0")"

REPO_NAME="${1:-ai-pulse}"

if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run:  gh auth login"
  exit 1
fi

echo "==> Ensuring git repo + initial commit"
if [ ! -d .git ]; then
  git init -b main
fi
git add -A
if ! git diff --cached --quiet; then
  git commit -m "AI Pulse: zero-cost self-updating AI news dashboard" >/dev/null
fi

echo "==> Creating GitHub repo '$REPO_NAME' (public) and pushing"
if gh repo view "$REPO_NAME" >/dev/null 2>&1; then
  echo "    repo exists — pushing to existing remote"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "$(gh repo view "$REPO_NAME" --json sshUrl -q .sshUrl)"
  git push -u origin main
else
  gh repo create "$REPO_NAME" --public --source=. --push \
    --description "AI Pulse — zero-cost, self-updating AI news dashboard (GitHub Actions + Pages)"
fi

echo "==> Enabling GitHub Pages (Actions build type)"
gh api --method POST "repos/{owner}/$REPO_NAME/pages" -f build_type=workflow 2>/dev/null \
  && echo "    Pages enabled" \
  || echo "    Pages already enabled (or will be auto-enabled by the workflow)"

echo "==> Triggering first workflow run"
sleep 3
gh workflow run update.yml --repo "$(gh repo view --json nameWithOwner -q .nameWithOwner)" || true

OWNER="$(gh api user -q .login)"
echo
echo "DONE."
echo "  Site:   https://$OWNER.github.io/$REPO_NAME/"
echo "  Runs:   gh run list --repo $OWNER/$REPO_NAME --workflow=update.yml"
echo "  Watch:  gh run watch --repo $OWNER/$REPO_NAME"
