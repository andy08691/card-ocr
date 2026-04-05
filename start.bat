@echo off
setlocal

:: Activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo ERROR: No virtual environment found. Please run the install steps first.
    exit /b 1
)

:: Load .env if it exists
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" (
            set "%%A=%%B"
        )
    )
)

:: Default values
if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=8000

echo Starting OCR server on http://%HOST%:%PORT%
uvicorn app.main:app --host %HOST% --port %PORT% --workers 1

endlocal
