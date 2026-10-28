@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   FreeToken Radar 一键发布
echo   改完页面后双击这个文件即可上线
echo ============================================
echo.

echo [1/4] 同步远端最新提交（避开定时任务的自动提交）
git pull --rebase --autostash
if errorlevel 1 (
  echo.
  echo [x] 拉取失败：本地可能有冲突，先手动处理再发布
  pause
  exit /b 1
)
echo.

echo [2/4] 抓取最新数据
python crawler\fetch_offers.py
if errorlevel 1 (
  echo.
  echo [x] 抓取失败，未推送任何改动
  pause
  exit /b 1
)
echo.

echo [3/4] 提交改动
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "update: %date% %time%"
) else (
  echo     没有改动，跳过提交
)
echo.

echo [4/4] 推送到 GitHub（Cloudflare 会自动重新部署）
git push
if errorlevel 1 (
  echo.
  echo [x] 推送失败，检查网络或仓库权限
  pause
  exit /b 1
)

echo.
echo [ok] 已推送，约 30 秒后线上生效。
echo      部署进度：https://dash.cloudflare.com  ^>  Workers 和 Pages
pause
