#!/usr/bin/with-contenv bashio

export NUKI_API_TOKEN=$(bashio::config 'nuki_api_token')
export NUKI_LOCK_ENTITIES=$(bashio::config 'lock_entities')

cd /app
python3 main.py
