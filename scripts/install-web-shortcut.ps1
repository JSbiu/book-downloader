$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Book Downloader.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path $shortcutPath) {
    $previous = $shell.CreateShortcut($shortcutPath)
    if ($previous.Arguments -notlike '*book_downloader.web*') {
        throw "The desktop shortcut already belongs to another application: $shortcutPath"
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wsl.exe'
$shortcut.Arguments = '-d Ubuntu-22.04 --cd /home/jason/projects/book-downloader --exec .venv/bin/python -m book_downloader.web --open'
$shortcut.Description = 'Book Downloader - local web interface'
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output "Desktop shortcut ready: $shortcutPath"
