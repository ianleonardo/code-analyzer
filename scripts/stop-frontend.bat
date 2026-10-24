@echo off
REM Stop the frontend listening on port 7778. Run from anywhere: scripts\stop-frontend.bat

setlocal
set "PORT=7778"
set "NAME=Frontend"
powershell -NoProfile -Command "& { $port = %PORT%; $name = '%NAME%'; $ids = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($ids.Count -eq 0) { Write-Host ($name + ' is not running.'); exit 0 }; $all = New-Object System.Collections.Generic.List[int]; foreach ($id in $ids) { [void]$all.Add([int]$id); $proc = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue; if (-not $proc) { continue }; $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $proc.ParentProcessId) -ErrorAction SilentlyContinue; if ($parent -and $parent.Name -match 'node|npm') { [void]$all.Add([int]$parent.ProcessId) } }; $all | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Write-Host ($name + ' stopped.') }"
endlocal
