$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$binDir = "C:\\Users\\diane\\alpha_code\\bin"
if ($userPath -notlike "*$binDir*") {
    $newPath = "$userPath;$binDir"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Host "PATH atualizado com sucesso!"
} else {
    Write-Host "bin ja esta no PATH"
}