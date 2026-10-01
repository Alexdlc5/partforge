@echo off
rem Double-click after cloning or unzipping PartForge. Runs setup.ps1 (it asks before installing anything).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
