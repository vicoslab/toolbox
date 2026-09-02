#!/usr/bin/env bash

supervisord -c /etc/supervisord.conf &2> /etc/supervisord.log
echo Waiting 10 seconds for server startup
sleep 10
echo Starting tests
uv run --extra test pytest -p no:cacheprovider || read -p "Test(s) failed. Press Enter to continue"
