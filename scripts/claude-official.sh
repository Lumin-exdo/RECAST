#!/usr/bin/env bash
set -euo pipefail

unset ANTHROPIC_BASE_URL
unset ANTHROPIC_AUTH_TOKEN
unset HTTP_PROXY
unset HTTPS_PROXY
unset ALL_PROXY
unset http_proxy
unset https_proxy
unset all_proxy
unset NO_PROXY
unset no_proxy

exec "$(dirname "$0")/us-sandbox.sh" claude "$@"
