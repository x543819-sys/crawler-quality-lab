@echo off
call "%~dp0launch.cmd" schedule --mode live --backend mysql --interval-seconds 1800 --run-now
