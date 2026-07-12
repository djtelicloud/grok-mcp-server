#!/bin/sh
set -eu

if [ "${CONTROL_CENTER_MODE:-}" = "github" ]; then
  missing=""
  [ -n "${APP_BASE_URL:-}" ] || missing="${missing} APP_BASE_URL"
  [ -n "${GITHUB_REPOSITORY:-}" ] || missing="${missing} GITHUB_REPOSITORY"
  [ -n "${GITHUB_REPOSITORY_ID:-}" ] || missing="${missing} GITHUB_REPOSITORY_ID"
  [ -n "${GITHUB_APP_ID:-}" ] || missing="${missing} GITHUB_APP_ID"
  [ -n "${GITHUB_APP_CLIENT_ID:-}" ] || missing="${missing} GITHUB_APP_CLIENT_ID"
  [ -n "${GITHUB_APP_INSTALLATION_ID:-}" ] || missing="${missing} GITHUB_APP_INSTALLATION_ID"
  [ -n "${GITHUB_APP_PRIVATE_KEY:-}" ] || missing="${missing} GITHUB_APP_PRIVATE_KEY"
  [ -n "${GITHUB_APP_CLIENT_SECRET:-}" ] || missing="${missing} GITHUB_APP_CLIENT_SECRET"
  [ -n "${AUTH_SESSION_SECRET:-}" ] || missing="${missing} AUTH_SESSION_SECRET"
  [ -n "${MCP_RESOURCE_URL:-}" ] || missing="${missing} MCP_RESOURCE_URL"
  [ -n "${MCP_TOKEN_SECRET:-}" ] || missing="${missing} MCP_TOKEN_SECRET"
  [ -n "${RECEIPT_SIGNING_KEY_ID:-}" ] || missing="${missing} RECEIPT_SIGNING_KEY_ID"
  [ -n "${RECEIPT_SIGNING_PRIVATE_KEY:-}" ] || missing="${missing} RECEIPT_SIGNING_PRIVATE_KEY"
  [ -n "${RECEIPT_SIGNING_PUBLIC_KEY:-}" ] || missing="${missing} RECEIPT_SIGNING_PUBLIC_KEY"

  if [ -n "${missing}" ]; then
    echo "GitHub control mode is missing required runtime configuration:${missing}" >&2
    exit 78
  fi
fi

exec node server.js
