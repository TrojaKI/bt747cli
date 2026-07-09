#!/usr/bin/env bash
# Convenience wrapper: run bt747cli from the project venv.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck disable=SC1091
source venv/bin/activate

if [[ $# -eq 0 ]]; then
  DATE=$(date +'%Y-%m-%d')
  bt747cli --help
  echo
  echo "e.g.:  $0 download --port /dev/ttyACM0 --output raw_qstarz2.bin"
  echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/ --split-days"
  echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/${DATE}.gpx --from ${DATE}"
  echo "e.g.:  $0 run --port /dev/ttyACM0 --save-bin raw_qstarz2.bin --output tracks/ --split-days"
  exit 2
fi

exec bt747cli "$@"
