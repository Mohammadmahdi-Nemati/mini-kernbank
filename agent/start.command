#!/bin/bash
# Startet Datenbank + Projekt-Agent für mini-kernbank → http://localhost:7861
# Ohne Doppelklick:  bash agent/start.command
cd "$(dirname "$0")" || exit 1
DSN_VORLAGE="postgresql://postgres:postgres@localhost:{PORT}/kernbank"
source ./start_kern.sh
