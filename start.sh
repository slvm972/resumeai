#!/bin/bash

# 🚀 ResumeAI - One-Click Startup Script
# This script will set up and run your entire application

echo "=================================="
echo "🚀 ResumeAI Startup Script"
echo "=================================="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.9 or higher."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo ""

# Navigate to backend directory
cd backend/

echo "📦 Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📚 Installing Python dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✅ Dependencies installed"

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "⚠️  WARNING: ANTHROPIC_API_KEY not set!"
    echo "The app will use demo mode without real AI analysis."
    echo ""
    echo "To enable AI features:"
    echo "1. Get API key from https://console.anthropic.com/"
    echo "2. Run: export ANTHROPIC_API_KEY='your-key-here'"
    echo "3. Re-run this script"
    echo ""
    read -p "Continue in demo mode? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create uploads directory
mkdir -p uploads

# Start backend in background
echo ""
echo "🚀 Starting backend server..."
python app.py &
BACKEND_PID=$!
echo "✅ Backend running on http://localhost:5000 (PID: $BACKEND_PID)"

# Wait for backend to start
sleep 3

# Start frontend
cd ../frontend/
echo ""
echo "🌐 Starting frontend server..."
echo "✅ Frontend will be available at http://localhost:8000"
echo ""
echo "=================================="
echo "✅ ResumeAI is now running!"
echo "=================================="
echo ""
echo "📱 Open your browser and go to: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop both servers"
echo ""

# Start frontend server
python3 -m http.server 8000

# Cleanup on exit
trap "kill $BACKEND_PID" EXIT
