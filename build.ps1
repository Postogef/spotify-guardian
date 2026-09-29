<#
  Gera o instalador dist\SpotifyGuardian-Setup.exe (+ .sha256 para o GitHub Releases).

  Uso (PowerShell, na pasta do projeto):
     .\build.ps1 -Repo dono/repositorio   # build para publicar (liga a atualizacao automatica)
     .\build.ps1 -Local                   # build de teste: dist\SpotifyGuardian-Setup-LOCAL.exe, sem atualizacao

  Precisa: Inno Setup 6 (winget install JRSoftware.InnoSetup) e o Python do PATH
  (so para o pip baixar as bibliotecas). A versao vem de VERSAO em src\guardian.py.
#>
param(
    [string]$Repo = '',
    [switch]$Local,
    [switch]$SemTestes
)

$ErrorActionPreference = 'Stop'
$Raiz = $PSScriptRoot
$Build = Join-Path $Raiz 'build'
$Cache = Join-Path $Raiz 'build_cache'
$Dist = Join-Path $Raiz 'dist'

function Passo([string]$t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Cyan }
function Falhar([string]$t) { Write-Host "ERRO: $t" -ForegroundColor Red; exit 1 }

# ---------- versao e repositorio ----------
$fonte = Get-Content (Join-Path $Raiz 'src\guardian.py') -Raw -Encoding UTF8
if ($fonte -notmatch '(?m)^VERSAO = "(\d+\.\d+\.\d+)"') { Falhar 'VERSAO nao encontrada em src\guardian.py' }
$Versao = $Matches[1]
if ($Repo -and $Local) { Falhar 'use -Repo (build para publicar) OU -Local (build de teste), nao os dois' }
if (-not $Repo -and -not $Local) { Falhar 'falta -Repo dono/repositorio (sem ele as lojas nunca se atualizam). Para um build de teste use -Local.' }
if ($Repo -and $Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { Falhar "Repo invalido: '$Repo' (use dono/repositorio)" }
$NomeSetup = if ($Local) { 'SpotifyGuardian-Setup-LOCAL' } else { 'SpotifyGuardian-Setup' }
if ($Repo) {
    # As lojas consultam a API do GitHub SEM login: repositorio privado ou inexistente = 404 = nunca atualiza.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        $null = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo" -UseBasicParsing -TimeoutSec 30 `
            -Headers @{ 'User-Agent' = 'SpotifyGuardian-build'; 'Accept' = 'application/vnd.github+json' }
    } catch {
        $cod = 0
        if ($_.Exception.Response) { $cod = [int]$_.Exception.Response.StatusCode }
        if ($cod -eq 404) { Falhar "o repositorio '$Repo' nao existe ou e privado: as lojas nao conseguiriam baixar as atualizacoes." }
        Write-Host "AVISO: nao consegui conferir o repositorio '$Repo' no GitHub ($($_.Exception.Message))." -ForegroundColor Yellow
    }
}

$Runtimes = @(
    @{ Nome = 'python38';  Versao = '3.8.10'; Abi = 'cp38'
       Url = 'https://www.python.org/ftp/python/3.8.10/python-3.8.10-embed-amd64.zip'
       Sha256 = 'abbe314e9b41603dde0a823b76f5bbbe17b3de3e5ac4ef06b759da5466711271'
       Pth = 'python38._pth'; Zip = 'python38.zip'; Req = 'requirements-py38.txt' },
    @{ Nome = 'python314'; Versao = '3.14.7'; Abi = 'cp314'
       Url = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip'
       Sha256 = 'd297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15'
       Pth = 'python314._pth'; Zip = 'python314.zip'; Req = 'requirements-py314.txt' }
)

$Iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { Falhar 'Inno Setup 6 nao encontrado. Instale: winget install JRSoftware.InnoSetup' }

Passo "Spotify Guardian $Versao  (repo de atualizacao: $(if ($Repo) { $Repo } else { 'nenhum' }))"
if (Test-Path $Build) { Remove-Item $Build -Recurse -Force }
$null = New-Item -ItemType Directory -Force -Path $Build, $Cache, $Dist, (Join-Path $Build 'app')

# ---------- runtimes Python embutidos ----------
foreach ($rt in $Runtimes) {
    Passo "Python $($rt.Versao) embutido ($($rt.Nome))"
    $zip = Join-Path $Cache (Split-Path $rt.Url -Leaf)
    if (-not (Test-Path $zip)) {
        Write-Host "Baixando $($rt.Url)"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        (New-Object Net.WebClient).DownloadFile($rt.Url, $zip)
    }
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $rt.Sha256) { Remove-Item $zip -Force; Falhar "SHA-256 de $zip nao confere ($hash)" }

    $dest = Join-Path $Build $rt.Nome
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    # sys.path: stdlib, a propria pasta, as bibliotecas e a pasta do guardian (..)
    Set-Content -Path (Join-Path $dest $rt.Pth) -Encoding ASCII -Value @($rt.Zip, '.', 'Lib\site-packages', '..')

    $site = Join-Path $dest 'Lib\site-packages'
    # --no-compile: o pip compilaria com o Python do PATH (outra versao); o compileall abaixo usa o certo
    & python -m pip install --disable-pip-version-check --no-warn-script-location --quiet `
        --no-compile --target $site --no-deps --only-binary=:all: `
        --python-version $rt.Versao --platform win_amd64 --implementation cp --abi $rt.Abi `
        -r (Join-Path $Raiz $rt.Req)
    if ($LASTEXITCODE -ne 0) { Falhar "pip falhou para $($rt.Nome)" }
    $bin = Join-Path $site 'bin'
    if (Test-Path $bin) { Remove-Item $bin -Recurse -Force }

    # confere se as bibliotecas abrem neste Python e pre-compila (.pyc)
    & (Join-Path $dest 'python.exe') -c "import spotipy, requests, urllib3, certifi, charset_normalizer; from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError; print('bibliotecas ok')"
    if ($LASTEXITCODE -ne 0) { Falhar "bibliotecas nao abrem no $($rt.Nome)" }
    & (Join-Path $dest 'python.exe') -m compileall -q $site
    if ($LASTEXITCODE -ne 0) { Falhar "alguma biblioteca nao compila no $($rt.Nome)" }
}

# ---------- codigo do guardian ----------
Passo 'Copiando o guardian'
$carimbado = $fonte -replace '(?m)^REPO_ATUALIZACAO = ".*"', ('REPO_ATUALIZACAO = "' + $Repo + '"')
[IO.File]::WriteAllText((Join-Path $Build 'app\guardian.py'), $carimbado, (New-Object Text.UTF8Encoding($false)))
Copy-Item (Join-Path $Raiz 'src\diagnostico.py') (Join-Path $Build 'app\diagnostico.py')

# ---------- testes nos dois Pythons ----------
if (-not $SemTestes) {
    foreach ($rt in $Runtimes) {
        Passo "Testes no Python $($rt.Versao)"
        & (Join-Path $Build "$($rt.Nome)\python.exe") (Join-Path $Raiz 'testes\testar_guardian.py') (Join-Path $Build 'app\guardian.py')
        if ($LASTEXITCODE -ne 0) { Falhar "testes falharam no Python $($rt.Versao)" }
    }
}

# ---------- instalador ----------
Passo 'Gerando o instalador (Inno Setup)'
& $Iscc /Q "/DAppVersion=$Versao" "/F$NomeSetup" (Join-Path $Raiz 'instalador\SpotifyGuardian.iss')
if ($LASTEXITCODE -ne 0) { Falhar 'Inno Setup falhou' }

$setup = Join-Path $Dist "$NomeSetup.exe"
$sha = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$setup.sha256", "$sha  SpotifyGuardian-Setup.exe`n", (New-Object Text.ASCIIEncoding))

Passo 'Pronto'
Write-Host "Instalador : $setup  ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB)"
Write-Host "SHA-256    : $sha"
if ($Local) {
    Write-Host 'Build de TESTE (-Local): sem atualizacao automatica. Nao publique este arquivo.' -ForegroundColor Yellow
} else {
    Write-Host "Versao     : $Versao   ->  publique no GitHub como release com a tag v$Versao"
    Write-Host "             anexando SpotifyGuardian-Setup.exe e SpotifyGuardian-Setup.exe.sha256"
}
