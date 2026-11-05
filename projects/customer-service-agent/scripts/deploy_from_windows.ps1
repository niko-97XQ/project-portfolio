param(
    [Parameter(Mandatory = $true)]
    [string]$ServerHost,
    [int]$ServerPort = 22,
    [string]$ServerUser = "ubuntu",
    [Parameter(Mandatory = $true)]
    [string]$SshKeyPath,
    [string]$RemoteDir = "/home/ubuntu/forthatday-agent-v2",
    [Parameter(Mandatory = $true)]
    [string]$EnvFilePath,
    [string]$PrivateDirPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$remote = "$ServerUser@$ServerHost"
$archive = Join-Path $env:TEMP "forthatday-agent-v2-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss')).tar.gz"
$remoteArchive = "/tmp/$(Split-Path -Leaf $archive)"

if (-not (Test-Path -LiteralPath $SshKeyPath)) {
    throw "SSH 私钥不存在：$SshKeyPath"
}
if (-not (Test-Path -LiteralPath $EnvFilePath)) {
    throw "环境文件不存在：$EnvFilePath"
}
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw "未找到 ssh.exe，请安装或启用 Windows OpenSSH Client"
}
if (-not (Get-Command scp.exe -ErrorAction SilentlyContinue)) {
    throw "未找到 scp.exe，请安装或启用 Windows OpenSSH Client"
}
if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
    throw "未找到 tar.exe，请安装 Windows 10/11 自带的 bsdtar"
}

try {
    Write-Host "[1/7] 检查 SSH 连接"
    & ssh.exe -i $SshKeyPath -p $ServerPort $remote "set -eu; mkdir -p '$RemoteDir'; echo SSH_OK"
    if ($LASTEXITCODE -ne 0) { throw "SSH 连接失败" }

    Write-Host "[2/7] 打包代码，排除运行时私有数据"
    & tar.exe -czf $archive -C $projectRoot --exclude=.venv --exclude=data --exclude=private --exclude=.env --exclude=.env.production .
    if ($LASTEXITCODE -ne 0) { throw "代码打包失败" }

    Write-Host "[3/7] 上传代码包"
    & scp.exe -i $SshKeyPath -P $ServerPort $archive "${remote}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw "代码包上传失败" }

    Write-Host "[4/7] 解压代码并清理临时包"
    $extract = "mkdir -p '$RemoteDir'; tar -xzf '$remoteArchive' -C '$RemoteDir'; rm -f '$remoteArchive'"
    & ssh.exe -i $SshKeyPath -p $ServerPort $remote $extract
    if ($LASTEXITCODE -ne 0) { throw "代码解压失败" }

    Write-Host "[5/7] 上传生产环境文件"
    & scp.exe -i $SshKeyPath -P $ServerPort $EnvFilePath "${remote}:$RemoteDir/.env.production"
    if ($LASTEXITCODE -ne 0) { throw "环境文件上传失败" }
    & ssh.exe -i $SshKeyPath -p $ServerPort $remote "chmod 600 '$RemoteDir/.env.production'"
    if ($LASTEXITCODE -ne 0) { throw "环境文件权限设置失败" }

    if ($PrivateDirPath) {
        if (-not (Test-Path -LiteralPath $PrivateDirPath)) {
            throw "私有目录不存在：$PrivateDirPath"
        }
        Write-Host "[6/7] 上传私有目录"
        $privateArchive = Join-Path $env:TEMP "forthatday-private-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss')).tar.gz"
        try {
            & tar.exe -czf $privateArchive -C (Resolve-Path $PrivateDirPath).Path .
            if ($LASTEXITCODE -ne 0) { throw "私有目录打包失败" }
            $privateRemoteArchive = "/tmp/$(Split-Path -Leaf $privateArchive)"
            & scp.exe -i $SshKeyPath -P $ServerPort $privateArchive "${remote}:$privateRemoteArchive"
            if ($LASTEXITCODE -ne 0) { throw "私有目录上传失败" }
            $privateExtract = "mkdir -p '$RemoteDir/private'; tar -xzf '$privateRemoteArchive' -C '$RemoteDir/private'; rm -f '$privateRemoteArchive'"
            & ssh.exe -i $SshKeyPath -p $ServerPort $remote $privateExtract
            if ($LASTEXITCODE -ne 0) { throw "私有目录解压失败" }
        }
        finally {
            if (Test-Path -LiteralPath $privateArchive) { Remove-Item -LiteralPath $privateArchive -Force }
        }
    }

    Write-Host "[7/7] 执行服务器端部署"
    & ssh.exe -i $SshKeyPath -p $ServerPort $remote "cd '$RemoteDir'; chmod +x scripts/deploy_remote.sh; APP_DIR='$RemoteDir' PUBLIC_URL='https://api.forthatday-studio.com' ./scripts/deploy_remote.sh"
    if ($LASTEXITCODE -ne 0) { throw "服务器端部署失败" }

    Write-Host "部署完成：$remote`:$RemoteDir"
}
finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
}
