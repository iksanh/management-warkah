@echo off
title Aplikasi Warkah - Kantah Bone Bolango
cd /d "%~dp0"

echo ============================================
echo   APLIKASI WARKAH - Kantah Bone Bolango
echo ============================================
echo.
echo Alamat untuk komputer ini : http://localhost:8000
echo Alamat untuk komputer lain di jaringan kantor:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo    http://%%a:8000
echo.
echo Biarkan jendela ini terbuka selama aplikasi dipakai.
echo Tekan Ctrl+C untuk menghentikan.
echo.

python app.py
pause
