# Creates "Tibia Loot List Manager" shortcuts on the desktop and in the Start menu for the portable app.
# Run through "Create desktop shortcut.cmd" in the portable folder; the shortcuts point into that folder,
# so run it again if you move the folder.
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string[]]$Folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$pythonw = Join-Path $Root 'python\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "This script must be run from the portable app folder (python\pythonw.exe not found in $Root)."
}
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $Folders) {
    if (-not $folder) { continue }
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $link = $shell.CreateShortcut((Join-Path $folder 'Tibia Loot List Manager.lnk'))
    $link.TargetPath = $pythonw
    $link.Arguments = '-m tibia_loot_manager'
    $link.WorkingDirectory = $Root
    $link.IconLocation = (Join-Path $Root 'TibiaLootManager.ico') + ',0'
    $link.Description = 'Tibia Quick Loot list manager'
    $link.Save()
    Write-Host "Shortcut created: $(Join-Path $folder 'Tibia Loot List Manager.lnk')"
}
