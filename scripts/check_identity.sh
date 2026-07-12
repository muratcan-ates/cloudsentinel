#!/bin/sh
# CloudSentinel contributor identity check.
# Run once per clone: verifies the local git identity is set before the
# first commit (a past typo cost hours) and installs the repo hooks.

set -e
cd "$(git rev-parse --show-toplevel)"

name=$(git config user.name || true)
email=$(git config user.email || true)

if [ -z "$name" ] || [ -z "$email" ]; then
  echo "identity check: git user.name / user.email are not set for this repo." >&2
  echo '  fix: git config --local user.name "<github-username>"' >&2
  echo '       git config --local user.email "<github-linked-email>"' >&2
  exit 1
fi

case "$email" in
  *@*) ;;
  *)
    echo "identity check: '$email' does not look like an email address." >&2
    exit 1
    ;;
esac

echo "identity ok: $name <$email>"
echo "reminder: the email must be linked to your GitHub account, or your"
echo "commits will not count toward the contributors graph."

git config core.hooksPath .githooks
echo "hooks installed: core.hooksPath -> .githooks"
