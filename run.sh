#!/usr/bin/bash


source venv/bin/activate

DATE=$(date +'%Y-%m-%d')

bt747cli $@


if [ $# -eq 0 ]; then
    echo
    echo "e.g.:  $0 download --port /dev/ttyACM0 --output raw_qstarz2.bin"
    echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/ --split-days"
    echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/$DATE.gpx --from $DATE"
    echo "e.g.:  $0 run --port /dev/ttyACM0 --save-bin raw_qstarz2.bin --output tracks/ --split-days"
    echo
fi

