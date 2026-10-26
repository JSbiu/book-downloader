@echo off
wsl.exe -d Ubuntu-22.04 --cd /home/jason/projects/book-downloader --exec .venv/bin/python -m book_downloader.web --open
if errorlevel 1 pause
