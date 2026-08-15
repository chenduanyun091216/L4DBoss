@echo off
chcp 65001 >nul
title L4DBoss 一键打包
cd /d "%~dp0"

echo ==============================================
echo   L4DBoss 一键打包程序
echo ==============================================
echo.

rem ---- 1. 选择 Python 解释器（优先 py 启动器，其次 python）----
py -c "import sys" >nul 2>&1
if errorlevel 1 goto :no_py
set "PY=py"
goto :py_ok

:no_py
python -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10 或更高版本。
    echo        安装时请勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)
set "PY=python"

:py_ok
echo [1/4] 使用 Python 解释器: %PY%

rem ---- 2. 安装项目依赖 ----
echo [2/4] 检查并安装依赖（PyQt5 / requests / vpk）...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接或 pip 配置。
    pause
    exit /b 1
)

rem ---- 3. 检查 Nuitka ----
echo [3/4] 检查打包工具 Nuitka ...
%PY% -m nuitka --version >nul 2>&1
if errorlevel 1 (
    echo        未检测到 Nuitka，正在安装 ...
    %PY% -m pip install nuitka
    if errorlevel 1 (
        echo.
        echo [错误] Nuitka 安装失败。
        pause
        exit /b 1
    )
)

rem ---- 4. 执行打包 ----
echo [4/4] 开始打包，需要几分钟，请耐心等待 ...
echo.
%PY% -m nuitka ^
  --mode=onefile ^
  --enable-plugin=pyqt5 ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=files/title.ico ^
  --include-data-dir=files=files ^
  --output-dir=build ^
  --output-filename=L4DBoss.exe ^
  run.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上方日志。
    pause
    exit /b 1
)

rem ---- 完成 ----
echo.
echo ==============================================
echo   打包完成！
echo ==============================================
if exist "build\L4DBoss.exe" (
    echo   输出文件: %cd%\build\L4DBoss.exe
    for %%F in ("build\L4DBoss.exe") do echo   文件大小: %%~zF bytes
) else (
    echo   输出文件未找到，请检查上方日志。
)
echo.
echo 双击 build\L4DBoss.exe 即可运行。
echo.
pause
exit /b 0
