#!/usr/bin/with-contenv bashio

export NUKI_API_TOKEN=$(bashio::config 'nuki_api_token')

cd /app
python3 main.py
