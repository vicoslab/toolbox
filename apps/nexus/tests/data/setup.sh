#! /usr/bin/env bash

cd "${0%/*}"

curl --remote-name 'https://data.vicos.si/slaif/cell-tile-demo.zip'
unzip cell-tile-demo.zip

curl --remote-name 'https://data.vicos.si/slaif/nanoparticles-demo.zip'
unzip nanoparticles-demo.zip