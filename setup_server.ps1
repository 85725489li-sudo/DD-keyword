Set-ExecutionPolicy Bypass -Scope Process -Force
$ErrorActionPreference = "Continue"
$ProjectDir = "D:\dd-keyword-tool"
$PythonExe = "C:\Python311\python.exe"

Write-Host "=== DD-Keyword-Tool 部署脚本 ===" -ForegroundColor Cyan

# 1. 检查/安装 Python
Write-Host "[1/5] 检查 Python..." -ForegroundColor Yellow
if (-not (Test-Path $PythonExe)) {
    Write-Host "下载安装 Python 3.11..."
    $installer = "$env:TEMP\python-installer.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $installer -UseBasicParsing
    Start-Process $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=C:\Python311" -Wait
    Write-Host "Python 安装完成" -ForegroundColor Green
} else {
    Write-Host "Python 已存在" -ForegroundColor Green
}

# 2. 安装 Git 并克隆代码
Write-Host "[2/5] 部署代码..." -ForegroundColor Yellow
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "下载安装 Git..."
    $gitInstaller = "$env:TEMP\git-installer.exe"
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/Git-2.44.0-64-bit.exe" -OutFile $gitInstaller -UseBasicParsing
    Start-Process $gitInstaller -ArgumentList "/SILENT" -Wait
    $env:PATH += ";C:\Program Files\Git\bin"
}
if (Test-Path "$ProjectDir\.git") {
    Write-Host "更新代码..."
    Set-Location $ProjectDir
    git pull origin main
} else {
    Write-Host "克隆代码..."
    Set-Location D:\
    git clone https://github.com/85725489li-sudo/DD-keyword.git dd-keyword-tool
}

# 3. 安装依赖
Write-Host "[3/5] 安装 Python 依赖..." -ForegroundColor Yellow
Set-Location $ProjectDir
& $PythonExe -m pip install -r requirements.txt -q
Write-Host "完成" -ForegroundColor Green

# 4. 安装 Playwright Chromium
Write-Host "[4/5] 安装 Chromium..." -ForegroundColor Yellow
& $PythonExe -m playwright install chromium
Write-Host "完成" -ForegroundColor Green

# 5. 创建启动文件
Write-Host "[5/5] 配置启动..." -ForegroundColor Yellow

$lines = @(
    "cd /d D:\dd-keyword-tool",
    "set PYTHONIOENCODING=utf-8",
    "set HEADLESS=1",
    "set DD_EMAIL=547142553@qq.com",
    "set DD_PASSWORD=wedobest123",
    "C:\Python311\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 >> D:\dd-keyword-tool\server.log 2>&1"
)
Set-Content -Path "$ProjectDir\start_server.bat" -Value $lines -Encoding ASCII

$vbs = 'Set WshShell = CreateObject("WScript.Shell")' + "`r`n" + 'WshShell.Run "cmd /c D:\dd-keyword-tool\start_server.bat", 0, False'
Set-Content -Path "$ProjectDir\start_server_hidden.vbs" -Value $vbs -Encoding ASCII

$startup = "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
Copy-Item "$ProjectDir\start_server_hidden.vbs" $startup -Force

# 开放防火墙
New-NetFirewallRule -DisplayName "DD-Keyword-8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -ErrorAction SilentlyContinue | Out-Null

# 启动服务
Write-Host "启动服务..." -ForegroundColor Yellow
Start-Process wscript.exe -ArgumentList "$ProjectDir\start_server_hidden.vbs"
Start-Sleep -Seconds 6

try {
    $r = Invoke-WebRequest "http://localhost:8000/api/queue-status" -UseBasicParsing -TimeoutSec 10
    Write-Host "SUCCESS 部署成功！访问 http://[服务器公网IP]:8000" -ForegroundColor Green
} catch {
    Write-Host "服务启动中，稍等几秒后访问 http://[服务器公网IP]:8000" -ForegroundColor Yellow
}
