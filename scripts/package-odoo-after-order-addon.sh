#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
addon_parent="$repository_root/odoo-addons"
artifact_dir="$repository_root/output"
artifact_path="$artifact_dir/after_order_portal.zip"

mkdir -p "$artifact_dir"
rm -f "$artifact_path"
(
  cd "$addon_parent"
  zip -qr "$artifact_path" after_order_portal -x '*/__pycache__/*' '*.pyc' '.DS_Store'
)
echo "$artifact_path"
