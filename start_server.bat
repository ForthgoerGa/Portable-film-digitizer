@echo off
cd /d "%~dp0software"
call venv\Scripts\activate.bat
python main.py
