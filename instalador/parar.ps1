# Para os Spotify Guardian rodando neste PC (versao nova e antigas):
#   - python/pythonw/py/pyw rodando um guardian.py
#   - guardian.exe (versao antiga feita com PyInstaller), so se a pasta dele tem config.json
# Usado pelo instalador, pelo desinstalador e pelo guardian.py --relogin / --reiniciar
# (que passa o proprio PID em -Exceto).
#   -Pasta   : pasta do guardian novo. Se o WMI nao responder, para os Pythons
#              que estao rodando de <Pasta>\python (plano B, sem WMI).
#   -SoPasta : para SO o guardian que roda de <Pasta> (o instalador usa antes de
#              copiar; os antigos, de outras pastas, so param quando o novo ja
#              esta instalado e configurado).
#   -SemAjudantes : nao para outras janelas --reiniciar / --relogin (usado por
#              elas mesmas: senao duas abertas juntas se matam e ninguem reabre
#              o guardian). O instalador nao usa: la todas precisam fechar.
# Compativel com o PowerShell 2.0 do Windows 7.
#   -SoAntigos : o contrario do -SoPasta: para so os guardians de OUTRAS pastas
#              (o instalador usa depois que o guardian novo ja abriu).
param([int]$Exceto = 0, [string]$Pasta = '', [switch]$SoPasta, [switch]$SemAjudantes, [switch]$SoAntigos)

$ErrorActionPreference = 'SilentlyContinue'
$WmiTravou = $false

# Get-WmiObject nao tem prazo: com o WMI travado ele esperaria para sempre.
# Roda num runspace separado e desiste depois de $segundos.
function Listar-Processos([int]$segundos) {
    $ps = [System.Management.Automation.PowerShell]::Create()
    $null = $ps.AddScript('Get-WmiObject Win32_Process | Select-Object ProcessId, Name, CommandLine, ExecutablePath')
    $h = $ps.BeginInvoke()
    if (-not $h.AsyncWaitHandle.WaitOne($segundos * 1000, $false)) { $script:WmiTravou = $true; return }
    $saida = $null
    try { $saida = $ps.EndInvoke($h) } catch { }
    $ps.Dispose()
    if ($saida) { foreach ($item in $saida) { $item } }
}

$raiz = ''
if ($Pasta) { $raiz = $Pasta.TrimEnd('\').ToLower() + '\' }
$parados = @()
$lista = @(Listar-Processos 60)

foreach ($p in $lista) {
    $id = [int]$p.ProcessId
    if ($id -eq $Exceto -or $id -eq $PID) { continue }
    $nome = [string]$p.Name
    $cmd = [string]$p.CommandLine
    $exe = [string]$p.ExecutablePath
    $ehGuardianPy = ($nome -match '^(pythonw?|pyw?)\.exe$') -and ($cmd -match 'guardian\.py')
    $ehGuardianExe = ($nome -ieq 'guardian.exe') -and $exe -and (Test-Path -LiteralPath (Join-Path (Split-Path $exe) 'config.json'))
    if (-not ($ehGuardianPy -or $ehGuardianExe)) { continue }
    if ($SemAjudantes -and $cmd -match '\s--(reiniciar|relogin)\b') { continue }
    if (($SoPasta -or $SoAntigos) -and $raiz) {
        $daPasta = $exe.ToLower().StartsWith($raiz) -or $cmd.ToLower().Contains($raiz)
        if ($SoPasta -and -not $daPasta) { continue }
        if ($SoAntigos -and $daPasta) { continue }
    }
    Stop-Process -Id $id -Force
    $parados += $id
}

# Plano B (WMI quebrado/travado: a lista vem vazia, pois nem este PowerShell aparece):
# para os Pythons que rodam da pasta do guardian novo.
if ($lista.Count -eq 0 -and $raiz -and -not $SoAntigos) {
    foreach ($proc in @(Get-Process -Name python, pythonw)) {
        if ($proc.Id -eq $Exceto -or $proc.Id -eq $PID) { continue }
        $caminho = [string]$proc.Path
        if ($caminho -and $caminho.ToLower().StartsWith($raiz + 'python\')) {
            Stop-Process -Id $proc.Id -Force
            $parados += $proc.Id
        }
    }
}

# Espera ate 15 s os processos sumirem (para liberar os arquivos da pasta).
$limite = (Get-Date).AddSeconds(15)
while ($parados.Count -gt 0 -and (Get-Date) -lt $limite) {
    $vivos = @($parados | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($vivos.Count -eq 0) { break }
    Start-Sleep -Milliseconds 300
}

# Com o WMI travado, 'exit' ficaria esperando o runspace preso: sai na marra.
Write-Output ('PARADOS=' + $parados.Count)   # o guardian le isso para anotar no log
if ($WmiTravou) { [Environment]::Exit(2) }
if ($lista.Count -eq 0) { exit 2 }  # WMI nao respondeu
exit 0
