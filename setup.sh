#!/bin/bash
# Quick setup script for local development

set -e

echo "=================================="
echo "OANDA Trading Bot - Quick Setup"
echo "=================================="

# Check Python version
echo ""
echo "[1/6] Checking Python version..."
python3 --version || { echo "❌ Python 3 not found. Please install Python 3.8+"; exit 1; }
echo "✅ Python OK"

# Create virtual environment (optional but recommended)
echo ""
echo "[2/6] Creating virtual environment (optional)..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

echo ""
echo "To activate the virtual environment:"
echo "  source venv/bin/activate"

# Install dependencies
echo ""
echo "[3/6] Installing dependencies..."
pip3 install -r requirements.txt
echo "✅ Dependencies installed"

# Create runtime directory
echo ""
echo "[4/6] Creating runtime directory..."
mkdir -p runtime
echo "✅ Runtime directory created"

# Create .env if it doesn't exist
echo ""
echo "[5/6] Setting up environment file..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ Created .env from template"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env and add your OANDA credentials:"
    echo "   nano .env"
else
    echo "✅ .env already exists"
fi

# Run tests
echo ""
echo "[6/6] Running validation tests..."
python3 test_bot.py

echo ""
echo "=================================="
echo "✅ Setup Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Add your OANDA credentials to .env:"
echo "   nano .env"
echo ""
echo "2. Test the bot in dry-run mode:"
echo "   python3 bot.py"
echo "   (Press Ctrl+C to stop after verifying it works)"
echo ""
echo "3. Run the dashboard:"
echo "   streamlit run dashboard.py"
echo "   (Open http://localhost:8501 in your browser)"
echo ""
echo "4. When ready, deploy to Render.com:"
echo "   - See DEPLOYMENT.md for full instructions"
echo ""
echo "⚠️  Remember: Always test with DRY_RUN=true first!"
echo "=================================="
