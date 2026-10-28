@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   FreeToken Radar  ^|  发布到 GitHub Pages
echo   目标：https://xiaoliuwx.github.io/freetoken-radar/
echo   通道：api.github.com（本机 github.com 不通也能用）
echo ============================================================
echo.

set "PY=python"
where python >nul 2>nul || set "PY=py"

if "%GITHUB_TOKEN%"=="" (
  %PY% deploy\prompt.py
) else (
  %PY% deploy\publish_api.py --owner xiaoliuwx --repo freetoken-radar
)

echo.
pause
