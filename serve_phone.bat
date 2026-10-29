@echo off
REM ---------------------------------------------------------------------------
REM  Starts your agent as a phone web app, reachable from your phone's browser.
REM  It reads agent_config.json (make it in the Builder: python builder.py), so
REM  the login token lives there, not in this file. Sign into Tailscale on this
REM  PC and your phone (same account), then open the 100.x address this prints.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
.venv\Scripts\python agent.py --config agent_config.json --serve
pause
