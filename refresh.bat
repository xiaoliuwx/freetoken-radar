@echo off
chcp 65001 >nul
cd /d "%~dp0"
python crawler\fetch_offers.py
echo.
echo 已刷新 data\offers.json，刷新浏览器即可看到最新数据
pause
