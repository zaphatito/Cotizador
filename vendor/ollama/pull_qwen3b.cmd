@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "OLLAMA_EXE=%SCRIPT_DIR%ollama.exe"
if not exist "%OLLAMA_EXE%" (
  echo ERROR: No encuentro ollama.exe en "%SCRIPT_DIR%"
  exit /b 1
)

for %%I in ("%SCRIPT_DIR%..\..") do set "APP_ROOT=%%~fI"

set "IS_DEV=0"
if exist "%APP_ROOT%\.git\" set "IS_DEV=1"

set "DEV_MODELS=%APP_ROOT%\vendor\ollama_models"
set "PROD_MODELS=%LOCALAPPDATA%\SistemaCotizaciones\ollama_models"

set "MODELS_DIR=%PROD_MODELS%"
if "%IS_DEV%"=="1" set "MODELS_DIR=%DEV_MODELS%"

if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%" >nul 2>nul

> "%MODELS_DIR%\.write_test" (echo ok) 2>nul
if errorlevel 1 (
  if "%IS_DEV%"=="1" (
    set "MODELS_DIR=%PROD_MODELS%"
    if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%" >nul 2>nul
    > "%MODELS_DIR%\.write_test" (echo ok) 2>nul
    if errorlevel 1 (
      echo ERROR: No tengo permisos de escritura en "%MODELS_DIR%".
      exit /b 1
    )
  ) else (
    echo ERROR: No tengo permisos de escritura en "%MODELS_DIR%".
    exit /b 1
  )
)
del /q "%MODELS_DIR%\.write_test" >nul 2>nul

set "OLLAMA_MODELS=%MODELS_DIR%"
set "OLLAMA_HOST=http://127.0.0.1:11434"

REM ---- Si no responde, levanta server en background (sin abrir otra ventana)
curl -s http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  echo [pull] Server no responde, levantando ollama serve...
  start "" /B "%OLLAMA_EXE%" serve 1>nul 2>nul

  set "OK=0"
  for /L %%i in (1,1,20) do (
    curl -s http://127.0.0.1:11434/api/tags >nul 2>nul && set "OK=1" && goto :READY
    timeout /t 1 >nul
  )
  :READY
  if "%OK%"=="0" (
    echo ERROR: no pude levantar el server en 20s.
    exit /b 1
  )
)

echo.
echo [pull] OLLAMA_HOST   = %OLLAMA_HOST%
echo [pull] OLLAMA_MODELS = %OLLAMA_MODELS%
echo.

"%OLLAMA_EXE%" pull qwen2.5:3b-instruct
if errorlevel 1 (
  echo ERROR: fallo el pull.
  exit /b 1
)

echo.
"%OLLAMA_EXE%" list
pause /b 0
