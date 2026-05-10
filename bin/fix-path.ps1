$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
# Remove quotes
$userPath = $userPath -replace '"', ''
# Normalize backslashes (collapse double backslashes)
while ($userPath -match '\\\\') {
    $userPath = $userPath -replace '\\\\', '\'
}
# Split, deduplicate, remove empties
$parts = $userPath -split ';' | Where-Object { $_ } | Select-Object -Unique
# Ensure bin is present
$binDir = "C:\Users\diane\alpha_code\bin"
if ($binDir -notin $parts) {
    $parts += $binDir
}
$newPath = $parts -join ';'
[Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
Write-Host "PATH corrigido:"
$parts | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "Feche e reabra o PowerShell para aplicar."
