#!/bin/bash

for YEAR in {2017..2024}; do

    mkdir -p $YEAR

    sed "s/YEAR/$YEAR/g" config-template.yaml > $YEAR/config_$YEAR.yaml

done
