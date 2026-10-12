# Run from a Visual Studio Developer PowerShell with the C++ compiler available.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Get-Command cl.exe -ErrorAction Stop | Out-Null
Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Force -Path '.local' | Out-Null
    & cl.exe /nologo /std:c++17 /EHsc /W4 /WX /Fe.local\monitor-state-test.exe /Fo.local\monitor-state-test.obj tests\firmware\monitor_state_test.cpp
    if ($LASTEXITCODE -ne 0) { throw 'ESP32 state test compilation failed.' }
    & .local\monitor-state-test.exe
    if ($LASTEXITCODE -ne 0) { throw 'ESP32 state regression test failed.' }
}
finally {
    Pop-Location
}
