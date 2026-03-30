# DD-Keyword-Tool 服务器一键部署脚本
# 在服务器上以管理员身份运行 PowerShell 执行此脚本

$ErrorActionPreference = "Continue"
$ProjectDir = "D:\dd-keyword-tool"
$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$PythonInstaller = "$env:TEMP\python-installer.exe"
$PythonExe = "C:\Python311\python.exe"

Write-Host "=== DD-Keyword-Tool 部署脚本 ===" -ForegroundColor Cyan

# 1. 检查/安装 Python
Write-Host "`n[1/5] 检查 Python..." -ForegroundColor Yellow
if (Test-Path $PythonExe) {
    Write-Host "Python 已安装" -ForegroundColor Green
} else {
    Write-Host "正在下载 Python 3.11..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonInstaller -UseBasicParsing
    Write-Host "正在安装 Python..."
    Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=C:\Python311" -Wait
    Write-Host "Python 安装完成" -ForegroundColor Green
}

# 2. 克隆/更新项目
Write-Host "`n[2/5] 部署项目代码..." -ForegroundColor Yellow
if (Test-Path "$ProjectDir\.git") {
    Write-Host "项目已存在，拉取最新代码..."
    cd $ProjectDir
    git pull origin main
} else {
    Write-Host "克隆项目..."
    # 先安装 git（如果没有）
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "下载安装 Git..."
        $GitUrl = "https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/Git-2.44.0-64-bit.exe"
        $GitInstaller = "$env:TEMP\git-installer.exe"
        Invoke-WebRequest -Uri $GitUrl -OutFile $GitInstaller -UseBasicParsing
        Start-Process -FilePath $GitInstaller -ArgumentList "/SILENT" -Wait
        $env:PATH += ";C:\Program Files\Git\bin"
    }
    New-Item -ItemType Directory -Path "D:\" -Force | Out-Null
    cd D:\
    git clone https://github.com/85725489li-sudo/DD-keyword.git dd-keyword-tool
}

# 3. 安装 Python 依赖
Write-Host "`n[3/5] 安装依赖..." -ForegroundColor Yellow
cd $ProjectDir
& $PythonExe -m pip install -r requirements.txt -q
Write-Host "依赖安装完成" -ForegroundColor Green

# 4. 安装 Playwright Chromium
Write-Host "`n[4/5] 安装 Chromium 浏览器..." -ForegroundColor Yellow
& $PythonExe -m playwright install chromium
Write-Host "Chromium 安装完成" -ForegroundColor Green

# 5. 创建启动脚本和开机自启
Write-Host "`n[5/5] 配置服务..." -ForegroundColor Yellow

# 创建启动 bat
$BatContent = @"
@echo off
cd /d D:\dd-keyword-tool
set PYTHONIOENCODING=utf-8
set HEADLESS=1
set DD_EMAIL=547142553@qq.com
set DD_PASSWORD=wedobest123
C:\Python311\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 >> D:\dd-keyword-tool\server.log 2>&1
"@
$BatContent | Out-File -FilePath "$ProjectDir\start_server.bat" -Encoding ASCII

# 创建隐藏窗口 VBS
$VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c D:\dd-keyword-tool\start_server.bat", 0, False
"@
$VbsContent | Out-File -FilePath "$ProjectDir\start_server_hidden.vbs" -Encoding ASCII

# 放入开机启动
$StartupPath = "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
Copy-Item "$ProjectDir\start_server_hidden.vbs" $StartupPath -Force
Write-Host "开机自启已配置" -ForegroundColor Green

# 开放防火墙
New-NetFirewallRule -DisplayName "DD-Keyword-Tool-8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -ErrorAction SilentlyContinue | Out-Null
Write-Host "防火墙端口 8000 已开放" -ForegroundColor Green

# 立即启动
Write-Host "`n正在启动服务..." -ForegroundColor Yellow
Start-Process -FilePath "wscript.exe" -ArgumentList "$ProjectDir\start_server_hidden.vbs"
Start-Sleep -Seconds 5

# 测试
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:8000/api/queue-status" -UseBasicParsing -TimeoutSec 10
    Write-Host "`n✅ 部署成功！" -ForegroundColor Green
    Write-Host "访问地址: http://[服务器IP]:8000" -ForegroundColor Cyan
} catch {
    Write-Host "`n⚠️ 服务启动中，请稍等片刻再访问" -ForegroundColor Yellow
}

Write-Host "`n完成！" -ForegroundColor Cyan
