@echo off
REM 🚀 ResumeAI - Windows Startup Script

echo ==================================
echo 🚀 ResumeAI Startup Script
echo ==================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python is not installed. Please install Python 3.9 or higher.
    pause
    exit /b 1
)

echo ✅ Python found
echo.

REM Navigate to backend directory
cd backend

echo 📦 Setting up Python virtual environment...
if not exist "venv" (
    python -m venv venv
    echo ✅ Virtual environment created
) else (
    echo ✅ Virtual environment already exists
)

REM Activate virtual environment
echo 🔧 Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo 📚 Installing Python dependencies...
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo ✅ Dependencies installed
echo.

REM Check for API key
if "%ANTHROPIC_API_KEY%"=="" (
    echo ⚠️  WARNING: ANTHROPIC_API_KEY not set!
    echo The app will use demo mode without real AI analysis.
    echo.
    echo To enable AI features:
    echo 1. Get API key from https://console.anthropic.com/
    echo 2. Run: set ANTHROPIC_API_KEY=your-key-here
    echo 3. Re-run this script
    echo.
    set /p continue="Continue in demo mode? (Y/N): "
    if /i not "%continue%"=="Y" exit /b 1
)

REM Create uploads directory
if not exist "uploads" mkdir uploads

REM Start backend
echo.
echo 🚀 Starting backend server...
start "ResumeAI Backend" cmd /k python app.py
echo ✅ Backend starting on http://localhost:5000

REM Wait for backend to start
timeout /t 3 /nobreak >nul

REM Start frontend
cd ..\frontend
echo.
echo 🌐 Starting frontend server...
echo ✅ Frontend will be available at http://localhost:8000
echo.
echo ==================================
echo ✅ ResumeAI is now running!
echo ==================================
echo.
echo 📱 Open your browser and go to: http://localhost:8000
echo.
echo Press Ctrl+C in the backend window to stop
echo.

REM Start frontend server
python -m http.server 8000

pause
