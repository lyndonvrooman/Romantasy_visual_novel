# Set execution working directories
$BaseDir = "C:\ai"
$ComfyDir = "$BaseDir\ComfyUI"
$PythonExe = "$ComfyDir\venv\Scripts\python.exe"

# 1. Start ComfyUI in its own console window
Write-Host "[*] Launching ComfyUI backend..." -ForegroundColor Cyan
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "Set-Location '$ComfyDir'; & '$PythonExe' main.py --directml --lowvram --listen 0.0.0.0 --port 8188"

# 2. Allow ComfyUI server to bind port before launching bot
Write-Host "[*] Waiting 5 seconds for ComfyUI initialization..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 3. Start the Discord Bot in a second console window
Write-Host "[*] Launching Discord Bot..." -ForegroundColor Green
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "Set-Location '$BaseDir'; & '$PythonExe' bot.py"

Write-Host "[+] All services spawned in background consoles." -ForegroundColor Green
