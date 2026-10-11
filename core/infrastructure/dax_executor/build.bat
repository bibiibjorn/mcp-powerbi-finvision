@echo off
REM Build script for DaxExecutor C# component

echo Building DaxExecutor...
dotnet build -c Release

if %ERRORLEVEL% NEQ 0 (
    echo Build failed!
    exit /b 1
)

echo Build successful!
echo Executable location: bin\Release\net8.0\DaxExecutor.exe
