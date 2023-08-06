#!/bin/bash

control_c() {
    exit
}

trap control_c SIGINT

while true; do
    source ~/.profile
    venv/bin/python main.py $1 $2
    echo "Crash found."
    for i in {5..1}; do
        echo  "Restarting in $i"
        sleep 1
    done
done

