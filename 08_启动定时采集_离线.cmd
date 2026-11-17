@echo off
call "%~dp0launch.cmd" schedule --mode demo --backend sqlite --interval-seconds 1800 --run-now
