#!/usr/bin/env bash
set -euo pipefail

coolify_keychain_service="${COOLIFY_KEYCHAIN_SERVICE:-codex.coolify.185.194.236.161}"

coolify_token="$(security find-generic-password -s "$coolify_keychain_service" -a coolify-api -w)"
coolify_host="$(security find-generic-password -s "$coolify_keychain_service" -a coolify-host -w)"
coolify_app="$(security find-generic-password -s "$coolify_keychain_service" -a coolify-app -w)"

if [[ -z "$coolify_token" || -z "$coolify_host" || -z "$coolify_app" ]]; then
  echo "Coolify Keychain credentials are incomplete for $coolify_keychain_service." >&2
  exit 1
fi

payload="$(jq -nc --arg uuid "$coolify_app" '{uuid:$uuid,force:true}')"
response="$(
  curl -fsS --connect-timeout 10 --max-time 30 \
    -X POST \
    -H "Authorization: Bearer $coolify_token" \
    -H "Content-Type: application/json" \
    --data "$payload" \
    "$coolify_host/api/v1/deploy"
)"

deployment_uuid="$(jq -r '.deployments[0].deployment_uuid // empty' <<<"$response")"
message="$(jq -r '.deployments[0].message // .message // "Coolify returned no deployment message."' <<<"$response")"

echo "$message"
if [[ -z "$deployment_uuid" ]]; then
  exit 1
fi
echo "Deployment UUID: $deployment_uuid"
