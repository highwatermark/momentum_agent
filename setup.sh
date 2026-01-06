#!/bin/bash
# Setup script for Momentum Trading Agent on Ubuntu 24.04

set -e

echo "=================================="
echo "Momentum Agent Setup"
echo "=================================="

cd ~/momentum-agent

# Create virtual environment if not exists
if [ ! -d "venv" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
echo "[2/4] Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create directories
mkdir -p data logs

# Set up systemd service
echo "[3/4] Setting up bot service..."
sudo cp momentum-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable momentum-agent

# Set up cron for scheduled scans
echo "[4/4] Setting up cron jobs..."
(crontab -l 2>/dev/null | grep -v "run_scan.sh" | grep -v "run_check.sh"; \
 echo "35 14 * * 1 cd ~/momentum-agent && ./venv/bin/python main.py scan >> logs/scan.log 2>&1"; \
 echo "55 20 * * 1-5 cd ~/momentum-agent && ./venv/bin/python main.py check >> logs/check.log 2>&1") | crontab -

echo ""
echo "=================================="
echo "Setup complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit .env with your API keys:"
echo "   nano .env"
echo ""
echo "2. Start the bot:"
echo "   sudo systemctl start momentum-agent"
echo ""
echo "3. Check status:"
echo "   sudo systemctl status momentum-agent"
echo ""
echo "4. View logs:"
echo "   journalctl -u momentum-agent -f"
echo ""
echo "5. Open Telegram and send /start to your bot"
echo ""

