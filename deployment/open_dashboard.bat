@echo off
setlocal

set "PROJECT=%~dp0.."
set "ENV_FILE=%PROJECT%\.env"
set PORT=8080
set TOKEN=

for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /i "%%A"=="REVIEW_SERVER_SECRET" set "TOKEN=%%B"
    if /i "%%A"=="REVIEW_SERVER_PORT"   set "PORT=%%B"
)

if "%TOKEN%"=="" (
    echo Could not read REVIEW_SERVER_SECRET from .env
    pause
    exit /b 1
)

start "" "http://localhost:%PORT%/?token=%TOKEN%"
endlocal
