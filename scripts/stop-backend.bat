@echo off
REM Stop the backend listening on port 8001. Run from anywhere: scripts\stop-backend.bat

setlocal
set "PORT=8001"
set "NAME=Backend"
powershell -NoProfile -Command "& { $port = %PORT%; $name = '%NAME%'; $ids = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($ids.Count -eq 0) { Write-Host ($name + ' is not running.'); exit 0 }; $all = New-Object System.Collections.Generic.List[int]; foreach ($id in $ids) { [void]$all.Add([int]$id); $proc = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue; if (-not $proc) { continue }; $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $proc.ParentProcessId) -ErrorAction SilentlyContinue; if ($parent -and $parent.Name -match 'uvicorn|python|node|npm') { [void]$all.Add([int]$parent.ProcessId) } }; $all | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Write-Host ($name + ' stopped.') }"
endlocal
