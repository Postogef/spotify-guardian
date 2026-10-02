# Acha uma instalacao ANTIGA do Spotify Guardian (pasta copiada na mao, com
# iniciar.bat / guardian.exe / guardian.py + config.json) e migra para a nova.
#
#   -Modo Detectar : so procura. Grava em -Estado "SIM" (achou config) ou "NAO"
#                    na 1a linha e o caminho da pasta antiga na 2a.
#   -Modo Migrar   : na 1a instalacao (pasta nova sem config.json), copia
#                    config.json e .cache_gef (login) da pasta antiga. Em toda
#                    instalacao/atualizacao, tira os atalhos antigos da
#                    inicializacao e remove entradas antigas do Registro (Run).
#                    Tudo fica anotado em <Novo>\instalacao.log.
#
# Nada e apagado: atalhos antigos vao para <Novo>\atalhos_antigos.
# Compativel com o PowerShell 2.0 do Windows 7 (.NET 2.0).
param(
    [Parameter(Mandatory = $true)][string]$Novo,
    [string]$Modo = 'Detectar',
    [string]$Estado = '',
    [string[]]$Raizes = @()   # so para testes: onde procurar (padrao: C:\, Areas de Trabalho, Documentos, Downloads)
)

$ErrorActionPreference = 'SilentlyContinue'
$Novo = $Novo.TrimEnd('\')
$ArqLog = Join-Path $Novo 'instalacao.log'
$ChaveRun = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$TemConfigNova = Test-Path -LiteralPath (Join-Path $Novo 'config.json')
$Avisos = @()
$WmiTravou = $false

function Registrar([string]$texto) {
    if ($Modo -eq 'Detectar') { return }
    $linha = '[' + (Get-Date -Format 'dd/MM/yyyy HH:mm:ss') + '] ' + $texto
    Add-Content -LiteralPath $ArqLog -Value $linha -Encoding UTF8
}

function Eh-PastaGuardian([string]$d) {
    if (-not $d) { return $false }
    $d = $d.TrimEnd('\')
    if ($d -ieq $Novo) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $d 'config.json'))) { return $false }
    return (Test-Path -LiteralPath (Join-Path $d 'guardian.py')) -or (Test-Path -LiteralPath (Join-Path $d 'guardian.exe'))
}

$Pontos = @{}
function Somar([string]$d, [int]$p) {
    if (-not (Eh-PastaGuardian $d)) { return }
    $k = $d.TrimEnd('\')
    if ($Pontos.ContainsKey($k)) { $Pontos[$k] = $Pontos[$k] + $p } else { $Pontos[$k] = $p }
}

# Caminho do guardian dentro de um texto de comando (com ou sem aspas).
function Caminho-NoComando([string]$texto) {
    if ($texto -match '"([^"]*(guardian\.py|guardian\.exe|iniciar\.bat))"') { return $matches[1] }
    if ($texto -match '(\S*(guardian\.py|guardian\.exe|iniciar\.bat))') { return $matches[1] }
    return $null
}

# Get-WmiObject nao tem prazo: com o WMI travado ele esperaria para sempre.
function Listar-Processos([int]$segundos) {
    $ps = [System.Management.Automation.PowerShell]::Create()
    $null = $ps.AddScript('Get-WmiObject Win32_Process | Select-Object ProcessId, Name, CommandLine, ExecutablePath')
    $h = $ps.BeginInvoke()
    if (-not $h.AsyncWaitHandle.WaitOne($segundos * 1000, $false)) { $script:WmiTravou = $true; return }  # travado: desiste
    $saida = $null
    try { $saida = $ps.EndInvoke($h) } catch { }
    $ps.Dispose()
    if ($saida) { foreach ($item in $saida) { $item } }
}

# ---- Modo Desfazer: o guardian novo nao abriu na 1a instalacao; religa o antigo ----
$PastaBackup = Join-Path $Novo 'atalhos_antigos'
$ArqRuns = Join-Path $PastaBackup 'run_antigos.txt'
if ($Modo -eq 'Desfazer') {
    $inicio = [Environment]::GetFolderPath('Startup')
    foreach ($lnk in @(Get-ChildItem -LiteralPath $PastaBackup -Filter '*.lnk' -Force)) {
        Move-Item -LiteralPath $lnk.FullName -Destination (Join-Path $inicio $lnk.Name) -Force
        Registrar ('Atalho antigo religado: ' + $lnk.Name)
    }
    if (Test-Path -LiteralPath $ArqRuns) {
        foreach ($linhaRun in @([System.IO.File]::ReadAllLines($ArqRuns))) {
            $partes = $linhaRun.Split("`t", 2)
            if ($partes.Count -eq 2) {
                Set-ItemProperty -LiteralPath $ChaveRun -Name $partes[0] -Value $partes[1]
                Registrar ('Entrada Run religada: ' + $partes[0])
            }
        }
        Remove-Item -LiteralPath $ArqRuns -Force
    }
    return
}

# ---- 1) atalhos nas pastas Inicializar (do usuario e de todos os usuarios) ----
$AtalhosAntigos = @()
$shell = New-Object -ComObject WScript.Shell
$pastaUsuario = [Environment]::GetFolderPath('Startup')     # existe no .NET 2.0
$pastaComum = $null                                         # 'CommonStartup' so existe no .NET 4
try { $pastaComum = [string]$shell.SpecialFolders.Item('AllUsersStartup') } catch { }
if (-not $pastaComum -and $env:ProgramData) { $pastaComum = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup' }
foreach ($pastaInicio in @($pastaUsuario, $pastaComum)) {
    if (-not $pastaInicio) { continue }
    foreach ($lnk in @(Get-ChildItem -LiteralPath $pastaInicio -Filter '*.lnk' -Force)) {
        if ($lnk.Extension -ne '.lnk') { continue }   # -Filter tambem pega nomes curtos 8.3 (ex.: x.lnkbak)
        $sc = $null
        try { $sc = $shell.CreateShortcut($lnk.FullName) } catch { continue }
        if (-not $sc) { continue }
        $alvo = [string]$sc.TargetPath
        $argumentos = [string]$sc.Arguments
        $dirTrabalho = [string]$sc.WorkingDirectory
        $pasta = $null
        $folha = ''
        if ($alvo) { $folha = Split-Path $alvo -Leaf }
        if ($folha -match '^(iniciar\.bat|guardian\.exe|guardian\.py)$') {
            $pasta = Split-Path $alvo
        } elseif ($folha -match '^(pythonw?|pyw?)(\.exe)?$' -and $argumentos -match 'guardian\.py') {
            $cam = Caminho-NoComando $argumentos
            if ($cam -and [System.IO.Path]::IsPathRooted($cam)) { $pasta = Split-Path $cam } else { $pasta = $dirTrabalho }
        }
        if ($pasta -and (Eh-PastaGuardian $pasta)) {
            Somar $pasta 10
            if ($pastaInicio -eq $pastaUsuario) {
                $AtalhosAntigos += $lnk.FullName
            } else {
                $Avisos += ('AVISO: atalho antigo em Inicializar (todos os usuarios) precisa de administrador para remover: ' + $lnk.FullName)
            }
        }
    }
}

# ---- 2) entradas antigas em HKCU\...\Run ----
$RunsAntigos = @()
$props = Get-ItemProperty -LiteralPath $ChaveRun
if ($props) {
    foreach ($prop in $props.PSObject.Properties) {
        if ($prop.Name -match '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { continue }
        $cam = Caminho-NoComando ([string]$prop.Value)
        if ($cam -and [System.IO.Path]::IsPathRooted($cam)) {
            $pasta = Split-Path $cam
            if (Eh-PastaGuardian $pasta) { Somar $pasta 10; $RunsAntigos += $prop.Name }
        }
    }
}

# ---- escolha da pasta antiga (so importa se a pasta nova ainda nao tem config) ----
$Melhor = $null
$Procurar = -not $TemConfigNova
if ($Modo -eq 'Migrar' -and $Estado -and (Test-Path -LiteralPath $Estado)) {
    # A deteccao desta mesma instalacao ja procurou (antes de parar os processos antigos).
    $Procurar = $false
    $linhas = @([System.IO.File]::ReadAllLines($Estado))
    if ($linhas.Count -ge 2 -and (Eh-PastaGuardian $linhas[1])) { $Melhor = $linhas[1].TrimEnd('\') }
}

if ($Procurar) {
    # ---- 3) guardian rodando agora (so da pra saber a pasta se o caminho for completo) ----
    foreach ($p in @(Listar-Processos 20)) {
        $nome = [string]$p.Name
        if ($nome -ieq 'guardian.exe' -and $p.ExecutablePath) {
            Somar (Split-Path ([string]$p.ExecutablePath)) 8
        } elseif ($nome -match '^(pythonw?|pyw?)\.exe$') {
            $cam = Caminho-NoComando ([string]$p.CommandLine)
            if ($cam -and [System.IO.Path]::IsPathRooted($cam)) { Somar (Split-Path $cam) 8 }
        }
    }

    # ---- 4) procura nas pastas mais provaveis (ate 3 niveis, no maximo 20 s) ----
    # Lista so PASTAS (GetDirectories): Get-ChildItem criaria um objeto para cada
    # arquivo e pastas de XML de NFC-e com milhares de arquivos travariam a busca.
    $Pulando = '^(\$.*|Windows|Program Files.*|ProgramData|Users|Documents and Settings|System Volume Information|Recovery|PerfLogs|AppData|node_modules|\.git|site-packages)$'
    $Relogio = [System.Diagnostics.Stopwatch]::StartNew()
    function Procurar-Em([string]$raiz, [int]$nivel) {
        if ($nivel -lt 0) { return }
        $subs = @()
        try { $subs = @((New-Object System.IO.DirectoryInfo $raiz).GetDirectories()) } catch { return }
        foreach ($sub in $subs) {
            if ($Relogio.ElapsedMilliseconds -gt 20000) { return }
            if ($sub.Name -match $Pulando) { continue }
            if (($sub.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            Somar $sub.FullName 1
            Procurar-Em $sub.FullName ($nivel - 1)
        }
    }
    if ($Raizes.Count -eq 0) {
        # pastas do usuario primeiro (onde a pasta antiga costuma ter sido copiada), C:\ por ultimo
        $Raizes = @([Environment]::GetFolderPath('Desktop'))
        if ($env:PUBLIC) { $Raizes += (Join-Path $env:PUBLIC 'Desktop') }
        $Raizes += [Environment]::GetFolderPath('MyDocuments')
        $Raizes += (Join-Path $env:USERPROFILE 'Downloads')
        $Raizes += ($env:SystemDrive + '\')
    }
    foreach ($r in $Raizes) {
        if ($r -and (Test-Path -LiteralPath $r)) {
            Somar $r 1   # pasta antiga solta direto na Area de Trabalho, por exemplo
            Procurar-Em $r 2
        }
    }
    if ($Relogio.ElapsedMilliseconds -gt 20000) { $Avisos += 'AVISO: busca por instalacao antiga parou no limite de 20 s.' }

    # ---- melhor pasta: mais pontos; empate -> login (.cache_gef) mais recente ----
    $lista = @()
    foreach ($k in @($Pontos.Keys)) {
        $pts = $Pontos[$k]
        $data = (Get-Item -LiteralPath (Join-Path $k 'config.json')).LastWriteTime
        $cache = Join-Path $k '.cache_gef'
        if (Test-Path -LiteralPath $cache) { $pts = $pts + 5; $data = (Get-Item -LiteralPath $cache -Force).LastWriteTime }
        $lista += New-Object PSObject -Property @{ Pasta = $k; Pontos = $pts; Data = $data }
    }
    $ordenada = @($lista | Sort-Object Pontos, Data -Descending)
    if ($ordenada.Count -gt 0) { $Melhor = $ordenada[0].Pasta }
}

if ($Modo -ne 'Migrar') {
    if ($Estado) {
        $res = 'NAO'
        if ($TemConfigNova -or $Melhor) { $res = 'SIM' }
        $conteudo = $res + "`r`n"
        if ($Melhor) { $conteudo = $conteudo + $Melhor + "`r`n" }
        [System.IO.File]::WriteAllText($Estado, $conteudo, (New-Object System.Text.UTF8Encoding($false)))
    }
    if ($WmiTravou) { [Environment]::Exit(0) }  # 'return' esperaria o runspace do WMI travado
    return
}

# ---------------- Modo Migrar ----------------
$null = New-Item -ItemType Directory -Path $Novo -Force
Registrar '=================================================='
Registrar 'Instalacao/atualizacao do Spotify Guardian'
foreach ($a in $Avisos) { Registrar $a }

if ($TemConfigNova) {
    # Atualizacao/reinstalacao: nao traz nada de pasta antiga. Se o .cache_gef
    # sumiu foi de proposito (login expirado, --relogin) e a pasta antiga pode
    # ate ser de outro posto.
    Registrar 'config.json ja existe na pasta nova; nada copiado de instalacao antiga.'
} elseif ($Melhor) {
    Registrar ('Instalacao antiga encontrada em: ' + $Melhor)
    $copias = @(
        @('config.json', 'config.json'),
        @('.cache_gef', '.cache_gef'),
        @('guardian.log', 'guardian_antigo.log')
    )
    foreach ($par in $copias) {
        $origem = Join-Path $Melhor $par[0]
        $destino = Join-Path $Novo $par[1]
        if ((Test-Path -LiteralPath $origem) -and -not (Test-Path -LiteralPath $destino)) {
            Copy-Item -LiteralPath $origem -Destination $destino -Force
            if (Test-Path -LiteralPath $destino) { Registrar ('Copiado: ' + $par[0]) } else { Registrar ('ERRO ao copiar: ' + $par[0]) }
        }
    }
} else {
    Registrar 'Nenhuma instalacao antiga encontrada.'
}

if ($AtalhosAntigos.Count -gt 0) {
    $backup = $PastaBackup
    $null = New-Item -ItemType Directory -Path $backup -Force
    foreach ($a in $AtalhosAntigos) {
        Move-Item -LiteralPath $a -Destination (Join-Path $backup (Split-Path $a -Leaf)) -Force
        if (Test-Path -LiteralPath $a) { Registrar ('ERRO ao desativar atalho antigo: ' + $a) } else { Registrar ('Atalho antigo desativado (movido para atalhos_antigos): ' + $a) }
    }
}

foreach ($nomeRun in $RunsAntigos) {
    $valor = (Get-ItemProperty -LiteralPath $ChaveRun -Name $nomeRun).$nomeRun
    $null = New-Item -ItemType Directory -Path $PastaBackup -Force
    [System.IO.File]::AppendAllText($ArqRuns, ($nomeRun + "`t" + $valor + "`r`n"))
    Remove-ItemProperty -LiteralPath $ChaveRun -Name $nomeRun
    if ((Get-ItemProperty -LiteralPath $ChaveRun -Name $nomeRun)) {
        Registrar ('ERRO ao remover entrada antiga do Registro (Run): ' + $nomeRun)
    } else {
        Registrar ('Entrada antiga removida do Registro (Run): ' + $nomeRun + ' = ' + $valor)
    }
}

if ($WmiTravou) {
    Registrar 'AVISO: o WMI do Windows nao respondeu a tempo.'
    [Environment]::Exit(0)  # 'exit' esperaria o runspace do WMI travado
}
