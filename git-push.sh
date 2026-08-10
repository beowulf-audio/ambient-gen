#!/bin/bash
# Helper script to push Ambient Generator TUI using PAT token
# Usage: ./git-push.sh [branch]
# Default branch: main

BRANCH="${1:-main}"
REPO_URL="github.com/beowulf-audio/ambient-gen.git"

# Read token from .git/credentials
TOKEN=$(cat .git/credentials 2>/dev/null | grep -o 'github_pat_[^@]*\|ghp_[^@]*')

if [ -z "$TOKEN" ]; then
    echo "❌ Error: No GitHub PAT token found in .git/credentials"
    echo "Please create .git/credentials with format:"
    echo "https://ghp_YOUR_TOKEN@github.com"
    exit 1
fi

echo "🔐 Configuring git remote with PAT..."

# Temporarily set remote URL with token
git remote set-url origin "https://${TOKEN}@${REPO_URL}"

echo "⬆️  Pushing to origin/${BRANCH}..."

# Push
git push -u origin "$BRANCH"
EXIT_CODE=$?

# Clean up: restore remote URL without token
echo "🧹 Cleaning up remote URL..."
git remote set-url origin "https://${REPO_URL}"

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Successfully pushed to ${BRANCH}!"
    echo "🚀 Check build status: https://github.com/beowulf-audio/ambient-gen/actions"
else
    echo "❌ Push failed with exit code ${EXIT_CODE}"
fi

exit $EXIT_CODE
