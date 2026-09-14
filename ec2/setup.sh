#!/bin/bash
# AMB Enterprise - EC2 Ubuntu/Amazon Linux One-Shot Setup
set -e

sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv ffmpeg wget unzip libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

python3 -m venv /opt/amb_venv
source /opt/amb_venv/bin/activate
pip install --upgrade pip
pip install -r requirements_server.txt
playwright install chromium
playwright install-deps chromium

if [ -f amb_enterprise.service ]; then
    sudo cp amb_enterprise.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable amb_enterprise.service
fi

echo Done. Run: python server.py --run-once
