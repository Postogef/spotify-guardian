; Instalador do Spotify Guardian GEF (Inno Setup 6.7 - gera setup que roda do Windows 7 ao 11).
; Nao compile direto: use o build.ps1 na raiz do projeto, que prepara a pasta build\
; (Python embutido + bibliotecas + guardian.py) e passa /DAppVersion.
;
; O que o instalador faz:
;   - Instala em %LOCALAPPDATA%\SpotifyGuardian, sem pedir administrador.
;   - Leva o Python junto: 3.8 no Windows 7/8, 3.14 no Windows 10/11.
;   - Para o guardian em execucao (novo ou antigo) antes de copiar e guarda o
;     Python atual de lado: se algo falhar, devolve o antigo e reabre o guardian.
;   - Reaproveita config.json e login (.cache_gef) de uma instalacao antiga
;     (pasta copiada na mao) e desativa o atalho antigo da inicializacao.
;   - Pede o codigo de 6 digitos do painel Musica GEF e, depois de copiar os arquivos,
;     pareia o PC (guardian.py --parear, pelo Python embutido: o Inno e o PowerShell do
;     Windows 7 nao garantem TLS 1.2). Pagina pulada se o PC ja esta pareado (painel.json).
;   - "Instalar sem o painel (avancado)": pede client_id / client_secret / playlist,
;     como antes (modo antigo). Pagina pulada se ja ha configuracao.
;   - Coloca o guardian na inicializacao do Windows e ja deixa rodando.
;   - Se o guardian antigo estiver na inicializacao de TODOS os usuarios, pede o
;     aceite de administrador (UAC) so para tirar esse atalho. O resto nao precisa.
; /CODIGO=123456 pareia com o painel sem perguntar nada (instalacao silenciosa).
; Parametros extras (usados nos testes): /SEMINICIAR (nao abre o guardian no fim) e
; /PASTACOMUM=pasta (pasta "Inicializar de todos os usuarios" de mentira, sem UAC).
; Codigos de saida: 8 = instalou, mas o pareamento pedido falhou; 9 = o guardian novo
; nao abriu (ver GetCustomSetupExitCode).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Spotify Guardian GEF"
#define Build "..\build"

[Setup]
AppId={{8F3C2A51-6B7D-4E0A-9C1E-5A2B7D9E4F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=GEF
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\SpotifyGuardian
DisableDirPage=yes
DefaultGroupName=Spotify Guardian
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.1sp1
OutputDir=..\dist
OutputBaseFilename=SpotifyGuardian-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupMutex=SpotifyGuardianSetupGEF
SetupLogging=yes
ShowLanguageDialog=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\python\pythonw.exe

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[InstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
; resto de uma instalacao interrompida
Type: filesandordirs; Name: "{app}\python.novo"

[Files]
; primeiro: copias usadas pelo proprio instalador antes de instalar (abrem rapido,
; sem precisar descompactar os 2 Pythons antes)
Source: "parar.ps1"; Flags: dontcopy
Source: "migrar.ps1"; Flags: dontcopy
; O Python novo e copiado para "python.novo" e so no fim troca de lugar com o "python"
; (TrocarPython). Assim a pasta "python" nunca fica pela metade: a tarefa agendada, que
; chama o pythonw.exe a cada 5 minutos, encontra sempre um Python inteiro ou nenhum.
Source: "{#Build}\python38\*"; DestDir: "{app}\python.novo"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: UsarPython38
Source: "{#Build}\python314\*"; DestDir: "{app}\python.novo"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: not UsarPython38
Source: "parar.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "migrar.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Build}\app\diagnostico.py"; DestDir: "{app}"; Flags: ignoreversion
; por ultimo: se algo falhar antes, o guardian.py antigo continua junto do Python antigo
Source: "{#Build}\app\guardian.py"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userstartup}\Spotify Guardian GEF"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\guardian.py"""; WorkingDir: "{app}"; Comment: "Mantem a playlist do GEF tocando"
Name: "{group}\Ver log do Spotify Guardian"; Filename: "{win}\notepad.exe"; Parameters: """{app}\guardian.log"""
Name: "{group}\Diagnostico do Spotify"; Filename: "{app}\python\python.exe"; Parameters: """{app}\diagnostico.py"""; WorkingDir: "{app}"
Name: "{group}\Conectar ao painel"; Filename: "{app}\python\python.exe"; Parameters: """{app}\guardian.py"" --conectar-painel"; WorkingDir: "{app}"
Name: "{group}\Refazer login do Spotify"; Filename: "{app}\python\python.exe"; Parameters: """{app}\guardian.py"" --relogin"; WorkingDir: "{app}"
Name: "{group}\Reiniciar Spotify Guardian"; Filename: "{app}\python\python.exe"; Parameters: """{app}\guardian.py"" --reiniciar"; WorkingDir: "{app}"
Name: "{group}\Editar configuracao (config.json)"; Filename: "{win}\notepad.exe"; Parameters: """{app}\config.json"""
Name: "{group}\Pasta do Spotify Guardian"; Filename: "{app}"
Name: "{group}\Desinstalar Spotify Guardian"; Filename: "{uninstallexe}"

; Sem [Run]: o guardian e aberto no fim do CurStepChanged(ssPostInstall), depois que
; o config.json foi migrado/gravado. Entradas [Run] sem 'postinstall' rodam ANTES
; do ssPostInstall e o guardian morreria sem config.

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\parar.ps1"" -Pasta ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "PararGuardian"

[UninstallDelete]
; config.json, painel.json (pareamento), .cache_gef (login) e os logs ficam, para uma reinstalacao
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\python.anterior*"
Type: filesandordirs; Name: "{app}\python.novo"
Type: files; Name: "{app}\*.anterior"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  PaginaPainel: TInputQueryWizardPage;
  CaixaSemPainel: TNewCheckBox;
  AvisoPainel: TNewStaticText;
  PaginaCred: TInputQueryWizardPage;
  JaDetectou: Boolean;
  AchouConfig: Boolean;
  PastaApp: String;
  PythonGuardado: String;
  GuardianParado: Boolean;
  InstalouOk: Boolean;
  VoltouVersao: Boolean;       { o guardian novo nao abriu e a versao anterior voltou }
  VersaoAnterior: String;      { versao instalada antes (registro), para desfazer }
  GuardianIniciado: Boolean;
  TinhaPython: Boolean;        { ja havia uma versao instalada nesta pasta (atualizacao) }
  NaoAbriu: Boolean;           { 1a instalacao: o guardian novo nao abriu; o antigo foi mantido }
  Pareou: Boolean;             { o PC foi pareado com o painel nesta instalacao }
  PareamentoFalhou: Boolean;   { pediram o pareamento e ele nao deu certo (codigo de saida 8) }
  PcNomePainel: String;        { nome do PC no painel (linha PCNOME= do --parear) }
  MensagemParear: String;      { 1a linha que o --parear escreveu (o motivo, se deu errado) }

const
  ChaveDesinstalar = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C2A51-6B7D-4E0A-9C1E-5A2B7D9E4F10}_is1';

function UsarPython38: Boolean;
var
  V: TWindowsVersion;
begin
  GetWindowsVersionEx(V);
  Result := V.Major < 10;   { Windows 7 e 8.x: Python 3.8; 10 e 11: Python 3.14 }
end;

function DeveIniciar: Boolean;
begin
  Result := Pos('/SEMINICIAR', UpperCase(GetCmdTail)) = 0;
end;

function PowerShell: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function RodarPS(const Script, Params: String): Integer;
var
  Codigo: Integer;
begin
  if not Exec(PowerShell, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Script + '" ' + Params,
              '', SW_HIDE, ewWaitUntilTerminated, Codigo) then
    Codigo := -1;
  Result := Codigo;
  Log(Format('PowerShell %s %s -> %d', [ExtractFileName(Script), Params, Codigo]));
end;

function ArquivoEstado: String;
begin
  Result := ExpandConstant('{tmp}\estado_migracao.txt');
end;

{ So para testes: /PASTACOMUM=pasta faz o papel da pasta Inicializar de todos os usuarios. }
function ParamPastaComum: String;
begin
  Result := ExpandConstant('{param:PASTACOMUM|}');
end;

function ParamsMigrar(const Pasta, Modo: String): String;
begin
  Result := '-Novo "' + Pasta + '" -Modo ' + Modo;
  if ParamPastaComum <> '' then
    Result := Result + ' -PastaComum "' + ParamPastaComum + '"';
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not RegQueryStringValue(HKCU, ChaveDesinstalar, 'DisplayVersion', VersaoAnterior) then
    VersaoAnterior := '';
  { Python 3.8 no Windows 7/8 precisa do Universal C Runtime (KB2999226) }
  if UsarPython38 and not FileExists(ExpandConstant('{sys}\ucrtbase.dll')) then
  begin
    Log('ATENCAO: ucrtbase.dll nao encontrado (falta a atualizacao KB2999226).');
    SuppressibleMsgBox('Este Windows nao tem o "Universal C Runtime" (atualizacao KB2999226 do ' +
      'Windows Update), que o Python precisa. Sem ele o Spotify Guardian nao abre.' + #13#10#13#10 +
      'A instalacao vai continuar, mas instale essa atualizacao depois.', mbError, MB_OK, IDOK);
  end;
end;

{ O guardian tem com o que trabalhar: config.json (modo antigo) ou painel.json (pareado).
  Numa atualizacao (ja havia versao instalada) abre sempre: o guardian que foi fechado
  pode estar esperando um pareamento (modo sem_conta) e precisa voltar a rodar. }
function TemConfiguracao: Boolean;
begin
  Result := FileExists(PastaApp + '\config.json') or FileExists(PastaApp + '\painel.json') or
            TinhaPython;
end;

procedure IniciarGuardian;
var
  Codigo: Integer;
begin
  if not DeveIniciar then
    Exit;
  if not TemConfiguracao then
  begin
    Log('Guardian nao iniciado: falta config.json ou painel.json.');
    Exit;
  end;
  if Exec(PastaApp + '\python\pythonw.exe', '"' + PastaApp + '\guardian.py" --sem-espera',
          PastaApp, SW_SHOWNORMAL, ewNoWait, Codigo) then
  begin
    GuardianIniciado := True;
    Log('Guardian iniciado.');
  end
  else
    Log('ERRO ao iniciar o guardian: ' + SysErrorMessage(Codigo));
end;

procedure LimparPythonsGuardados;
var
  R: TFindRec;
begin
  if FindFirst(PastaApp + '\python.anterior*', R) then
  try
    repeat
      if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        if not DelTree(PastaApp + '\' + R.Name, True, True, True) then
          Log('Python antigo ainda em uso, fica para a proxima: ' + R.Name);
    until not FindNext(R);
  finally
    FindClose(R);
  end;
end;

{ Ja existe config.json ou painel.json na pasta nova, ou config.json numa instalacao antiga? }
function ConfigExiste: Boolean;
var
  Estado: AnsiString;
begin
  if not JaDetectou then
  begin
    JaDetectou := True;
    AchouConfig := FileExists(AddBackslash(WizardDirValue) + 'config.json') or
                   FileExists(AddBackslash(WizardDirValue) + 'painel.json');
    if not AchouConfig then
    begin
      ExtractTemporaryFile('migrar.ps1');
      RodarPS(ExpandConstant('{tmp}\migrar.ps1'),
              ParamsMigrar(WizardDirValue, 'Detectar') + ' -Estado "' + ArquivoEstado + '"');
      if LoadStringFromFile(ArquivoEstado, Estado) then
        AchouConfig := Copy(Estado, 1, 3) = 'SIM';
    end;
    Log('Configuracao existente encontrada: ' + IntToStr(Ord(AchouConfig)));
  end;
  Result := AchouConfig;
end;

function EhHex32(const S: String): Boolean;
var
  I: Integer;
begin
  Result := Length(S) = 32;
  if Result then
    for I := 1 to Length(S) do
      if Pos(Lowercase(S[I]), '0123456789abcdef') = 0 then
      begin
        Result := False;
        Exit;
      end;
end;

function LimparTexto(const S: String): String;
begin
  Result := Trim(S);
  StringChangeEx(Result, '"', '', True);
  StringChangeEx(Result, '\', '', True);
end;

{ Codigo do painel sem espacos, pontos e tracos; '' se nao sobrarem exatamente 6
  digitos. So isso vai para a linha de comando do Python. }
function LimparCodigo(const S: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
    if (S[I] >= '0') and (S[I] <= '9') then
      Result := Result + S[I]
    else if Pos(S[I], ' .-') = 0 then
    begin
      Result := '';
      Exit;
    end;
  if Length(Result) <> 6 then
    Result := '';
end;

{ Marcaram "Instalar sem o painel": modo antigo, com Client ID e Client Secret. }
function SemPainel: Boolean;
begin
  Result := False;
  if CaixaSemPainel <> nil then
    Result := CaixaSemPainel.Checked;
end;

{ Codigo digitado na pagina do painel (ou /CODIGO=); '' = nao parear. }
function CodigoPedido: String;
begin
  Result := '';
  if (PaginaPainel <> nil) and not SemPainel then
    Result := Trim(PaginaPainel.Values[0]);
end;

{ O PC ja foi pareado e depois desconectado do painel (o guardian esta esperando um codigo novo). }
function PcDesconectado: Boolean;
begin
  Result := FileExists(AddBackslash(WizardDirValue) + 'painel.json.revogado');
end;

procedure AtualizarPaginaPainel(Sender: TObject);
begin
  PaginaPainel.Edits[0].Enabled := not SemPainel;
  { sem o painel e sem configuracao, a proxima pagina e a do Client ID / Client Secret }
  if SemPainel and not ConfigExiste then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonNext)
  else
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
end;

procedure InitializeWizard;
begin
  PaginaPainel := CreateInputQueryPage(wpSelectDir,
    'Conectar ao painel Música GEF',
    'Código de pareamento deste PC',
    'No painel Música GEF, abra a página deste PC e copie o código de pareamento. ' +
    'O código vale 24 horas e só pode ser usado uma vez.');
  PaginaPainel.Add('Código do painel (6 dígitos):', False);
  PaginaPainel.Values[0] := ExpandConstant('{param:CODIGO|}');

  CaixaSemPainel := TNewCheckBox.Create(PaginaPainel);
  CaixaSemPainel.Top := PaginaPainel.Edits[0].Top + PaginaPainel.Edits[0].Height + ScaleY(20);
  CaixaSemPainel.Width := PaginaPainel.SurfaceWidth;
  CaixaSemPainel.Height := ScaleY(17);
  CaixaSemPainel.Caption := 'Instalar sem o painel (avançado: Client ID e Client Secret)';
  CaixaSemPainel.OnClick := @AtualizarPaginaPainel;
  CaixaSemPainel.Parent := PaginaPainel.Surface;

  AvisoPainel := TNewStaticText.Create(PaginaPainel);
  AvisoPainel.AutoSize := False;
  AvisoPainel.WordWrap := True;
  AvisoPainel.Top := CaixaSemPainel.Top + CaixaSemPainel.Height + ScaleY(16);
  AvisoPainel.Width := PaginaPainel.SurfaceWidth;
  AvisoPainel.Parent := PaginaPainel.Surface;

  PaginaCred := CreateInputQueryPage(PaginaPainel.ID,
    'Dados do Spotify deste posto',
    'Nao achei uma configuracao anterior neste computador.',
    'Copie os dados do app no painel do Spotify for Developers ' +
    '(developer.spotify.com/dashboard). Cada posto tem os seus.');
  PaginaCred.Add('Client ID (32 caracteres):', False);
  PaginaCred.Add('Client Secret (32 caracteres):', False);
  PaginaCred.Add('Playlist (link ou spotify:playlist:...):', False);
  PaginaCred.Values[2] := 'spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PaginaPainel <> nil) and (PageID = PaginaPainel.ID) then
  begin
    { a procura por uma instalacao antiga (ate 20 s) roda aqui, antes de a pagina
      aparecer: o aviso dela depende disso }
    ConfigExiste;
    Result := FileExists(AddBackslash(WizardDirValue) + 'painel.json');
  end;
  if (PaginaCred <> nil) and (PageID = PaginaCred.ID) then
    Result := ConfigExiste or not SemPainel;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { instalacao silenciosa (a atualizacao automatica) nunca para pedindo nada:
    sem /CODIGO= ela so nao pareia }
  if WizardSilent then
    Exit;
  if (PaginaPainel <> nil) and (CurPageID = PaginaPainel.ID) and not SemPainel then
  begin
    if CodigoPedido = '' then
    begin
      { em branco: so pergunta se nao ha uma configuracao para continuar como esta }
      if not ConfigExiste or PcDesconectado then
        Result := MsgBox('Continuar sem conectar ao painel?' + #13#10#13#10 +
          'O Spotify Guardian fica instalado, mas só cuida da música depois de conectado ' +
          '(menu Iniciar > Spotify Guardian > Conectar ao painel).',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    end
    else if LimparCodigo(CodigoPedido) = '' then
    begin
      MsgBox('O código do painel tem 6 dígitos (só números).', mbError, MB_OK);
      Result := False;
    end;
  end;
  if (PaginaCred <> nil) and (CurPageID = PaginaCred.ID) then
  begin
    if not EhHex32(Trim(PaginaCred.Values[0])) or not EhHex32(Trim(PaginaCred.Values[1])) then
    begin
      MsgBox('Client ID e Client Secret tem 32 caracteres cada (numeros e letras de a ate f).' + #13#10 +
             'Copie do painel do Spotify for Developers.', mbError, MB_OK);
      Result := False;
    end
    else if LimparTexto(PaginaCred.Values[2]) = '' then
    begin
      MsgBox('Informe a playlist do posto.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

const
  ArquivosApp = 'guardian.py,diagnostico.py,parar.ps1,migrar.ps1';

{ Guarda (Acao=0), devolve (Acao=1) ou apaga (Acao=2) as copias .anterior dos
  arquivos do guardian: se a instalacao falhar depois de copia-los, o guardian
  antigo volta inteiro (Python + .py). }
procedure CopiasAnteriores(Acao: Integer);
var
  Lista: TStringList;
  I: Integer;
  Arq: String;
begin
  Lista := TStringList.Create;
  try
    Lista.CommaText := ArquivosApp;
    for I := 0 to Lista.Count - 1 do
    begin
      Arq := PastaApp + '\' + Lista[I];
      case Acao of
        0: begin
             DeleteFile(Arq + '.anterior');  { nunca reaproveitar uma copia velha }
             if FileExists(Arq) and not FileCopy(Arq, Arq + '.anterior', False) then
               Log('ERRO ao guardar copia de ' + Lista[I]);
           end;
        1: if FileExists(Arq + '.anterior') then
           begin
             if FileCopy(Arq + '.anterior', Arq, False) then
             begin
               DeleteFile(Arq + '.anterior');
               Log('Restaurado: ' + Lista[I]);
             end
             else
               Log('ERRO ao restaurar ' + Lista[I] + ' (a copia ficou em ' + Lista[I] + '.anterior)');
           end;
        2: DeleteFile(Arq + '.anterior');
      end;
    end;
  finally
    Lista.Free;
  end;
end;

{ Tarefa agendada que o guardian cria para se reabrir sozinho (uma por usuario). }
procedure RemoverTarefaVigia;
var
  Codigo: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'),
       '/Delete /TN "Spotify Guardian GEF (' + ExpandConstant('{username}') + ')" /F',
       '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  if Codigo = 0 then
    Log('Tarefa agendada removida.')
  else
    Log(Format('Tarefa agendada nao removida (nao existia ou falhou: schtasks -> %d).', [Codigo]));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  Result := '';
  PastaApp := ExpandConstant('{app}');
  { Fecha SO o guardian desta pasta (e o que prende os arquivos). Guardians antigos
    de outras pastas continuam tocando ate o novo estar instalado e configurado:
    se a instalacao falhar ou for cancelada, a loja nao fica sem nenhum. }
  ExtractTemporaryFile('parar.ps1');
  RodarPS(ExpandConstant('{tmp}\parar.ps1'), '-Pasta "' + PastaApp + '" -SoPasta');
  for I := 1 to 40 do
  begin
    if not CheckForMutexes('Local\SpotifyGuardianGEF') then
      Break;
    Sleep(500);
  end;
  if CheckForMutexes('Local\SpotifyGuardianGEF') then
  begin
    Log('O guardian em execucao nao fechou; instalacao cancelada.');
    Result := 'O Spotify Guardian em execucao nao fechou. ' +
              'Reinicie o computador e rode o instalador de novo.';
    Exit;
  end;
  GuardianParado := True;
  { Enquanto os arquivos sao trocados, a tarefa agendada nao pode abrir um
    pythonw.exe desta pasta (--vigia, a cada 5 minutos): ele nasceria no meio da
    troca do Python. O guardian recria a tarefa assim que abre (o novo, ou o
    anterior se a instalacao for desfeita). }
  RemoverTarefaVigia;
  TinhaPython := DirExists(PastaApp + '\python');
  CopiasAnteriores(0);
  { O Python atual fica onde esta ate o novo estar inteiro em "python.novo"
    (ver TrocarPython): queda de energia ou erro no meio da copia nao estraga nada. }
end;

{ Troca o Python: o atual vai para "python.anterior-<data>" (da para renomear a pasta
  mesmo com algo rodando dela, ex.: janela do Diagnostico) e o "python.novo" vira
  "python". Sao so dois renomeios: a pasta "python" nunca fica pela metade. Se a
  instalacao nao der certo depois, o DeinitializeSetup devolve o anterior.

  Cada renomeio insiste por ate 10 s: ele falha (acesso negado) enquanto algum arquivo
  da pasta esta sendo aberto, nem que seja por um instante (um python abrindo, o
  antivirus conferindo os arquivos recem-copiados). Por isso tambem a tarefa
  agendada e removida no PrepareToInstall: ela abriria um pythonw.exe daqui. }
function RenomearPasta(const De, Para: String): Boolean;
var
  I: Integer;
begin
  Result := True;
  for I := 1 to 20 do
  begin
    if RenameFile(De, Para) then
    begin
      if I > 1 then
        Log(Format('Pasta %s estava em uso: so foi renomeada na tentativa %d.', [ExtractFileName(De), I]));
      Exit;
    end;
    if I < 20 then
      Sleep(500);
  end;
  Result := False;
end;

function TrocarPython: Boolean;
begin
  Result := False;
  if DirExists(PastaApp + '\python') then
  begin
    PythonGuardado := PastaApp + '\python.anterior-' + GetDateTimeString('yyyymmddhhnnss', #0, #0);
    if not RenomearPasta(PastaApp + '\python', PythonGuardado) then
    begin
      Log('ERRO ao guardar o Python anterior.');
      PythonGuardado := '';
      Exit;
    end;
    Log('Python anterior guardado em ' + PythonGuardado);
  end;
  Result := RenomearPasta(PastaApp + '\python.novo', PastaApp + '\python');
  if not Result then
    Log('ERRO ao ativar o Python novo (python.novo -> python).');
end;

function MontarConfig: String;
var
  NL: String;
begin
  NL := #13#10;
  Result :=
    '{' + NL +
    '    "client_id": "' + Lowercase(Trim(PaginaCred.Values[0])) + '",' + NL +
    '    "client_secret": "' + Lowercase(Trim(PaginaCred.Values[1])) + '",' + NL +
    '    "redirect_uri": "http://127.0.0.1:8888/callback",' + NL +
    '    "playlist_uri": "' + LimparTexto(PaginaCred.Values[2]) + '",' + NL +
    '    "embaralhar": true,' + NL +
    '    "check_interval": 180,' + NL +
    '    "startup_delay": 60,' + NL +
    '    "horario_inicio": null,' + NL +
    '    "horario_fim": null' + NL +
    '}' + NL;
end;

{ O guardian cria o mutex logo no inicio: se ele nao aparece, o guardian novo
  morreu ao abrir (biblioteca/DLL incompativel com este Windows, etc.). }
const
  EsperaGuardian = 360;   { segundos para o guardian novo abrir (PC fraco demora) }

function GuardianSubiu(Segundos: Integer): Boolean;
var
  I: Integer;
begin
  Result := True;
  for I := 1 to Segundos * 2 do
  begin
    if CheckForMutexes('Local\SpotifyGuardianGEF') then
      Exit;
    Sleep(500);
  end;
  Result := CheckForMutexes('Local\SpotifyGuardianGEF');
end;

{ So agora a versao anterior pode ser descartada. }
procedure ConfirmarInstalacao;
begin
  InstalouOk := True;
  LimparPythonsGuardados;
  CopiasAnteriores(2);
end;

{ O atalho do guardian antigo na pasta Inicializar de TODOS os usuarios so sai com
  permissao de administrador. O instalador inteiro nao roda como administrador (a
  atualizacao automatica pararia num pedido de permissao a cada versao): o aceite
  do Windows (UAC) e pedido so para este passo, e so quando esse atalho existe.
  Sem o aceite nada quebra: o guardian novo fecha o antigo sozinho a cada login. }
procedure DesativarAntigoDeTodosOsUsuarios;
var
  Pendente, Params: String;
  Codigo: Integer;
begin
  Pendente := PastaApp + '\pendente_admin.txt';
  if not FileExists(Pendente) then
    Exit;
  Params := ParamsMigrar(PastaApp, 'Admin');
  if ParamPastaComum <> '' then
    RodarPS(PastaApp + '\migrar.ps1', Params)   { testes: pasta de mentira, nao precisa de UAC }
  else
  begin
    if WizardSilent then
    begin
      Log('Guardian antigo na inicializacao de TODOS os usuarios: precisa de administrador (instalacao silenciosa: nao pedi).');
      Exit;
    end;
    MsgBox('O Spotify Guardian antigo tambem esta na inicializacao de TODOS os usuarios deste computador.' + #13#10#13#10 +
           'Para desativar, o Windows vai pedir permissao de administrador: clique em "Sim" na proxima janela.',
           mbInformation, MB_OK);
    if not ShellExec('runas', PowerShell,
         '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + PastaApp + '\migrar.ps1" ' + Params,
         '', SW_HIDE, ewWaitUntilTerminated, Codigo) then
      Log('Permissao de administrador negada (ou falhou): ' + SysErrorMessage(Codigo));
  end;
  if FileExists(Pendente) then
  begin
    Log('O guardian antigo continua na inicializacao de todos os usuarios.');
    SuppressibleMsgBox('Nao foi possivel tirar o guardian antigo da inicializacao de todos os usuarios.' + #13#10#13#10 +
      'Nada para de funcionar: ele continua abrindo a cada login e o guardian novo fecha ele sozinho.' + #13#10 +
      'Para resolver de vez, rode este instalador de novo e aceite a permissao.', mbError, MB_OK, IDOK);
  end
  else
    Log('Guardian antigo de todos os usuarios desativado.');
end;

{ Ha atalhos / entradas Run do guardian antigo guardados em atalhos_antigos? }
function TemAtalhosGuardados: Boolean;
var
  R: TFindRec;
begin
  Result := FileExists(PastaApp + '\atalhos_antigos\run_antigos.txt');
  if not Result and FindFirst(PastaApp + '\atalhos_antigos\*.lnk', R) then
  begin
    Result := True;
    FindClose(R);
  end;
end;

{ O guardian novo nao abriu (ou o Python novo nem rodou). Atualizacao: volta para a
  versao anterior. 1a instalacao: mantem o guardian antigo, se havia. }
procedure NovoNaoAbriu(const JaTinhaAtalhos: Boolean);
begin
  RodarPS(PastaApp + '\parar.ps1', '-Pasta "' + PastaApp + '" -SoPasta');
  if TinhaPython then
  begin
    Log('Voltando para a versao anterior.');
    { a "versao anterior" pode ser uma 1a instalacao que nunca abriu (instalador
      rodado de novo): se foi ESTA instalacao que tirou o guardian antigo da
      inicializacao, religa os atalhos dele }
    if not JaTinhaAtalhos and TemAtalhosGuardados then
      RodarPS(PastaApp + '\migrar.ps1', ParamsMigrar(PastaApp, 'Desfazer'));
    VoltouVersao := True;
    Exit;  { InstalouOk continua False: o DeinitializeSetup restaura a versao anterior }
  end;
  { 1a instalacao: o guardian antigo nem foi parado; religa os atalhos dele }
  Log('Mantendo o guardian antigo.');
  RodarPS(PastaApp + '\migrar.ps1', ParamsMigrar(PastaApp, 'Desfazer'));
  NaoAbriu := True;
  GuardianIniciado := False;
  ConfirmarInstalacao;
  SuppressibleMsgBox('O Spotify Guardian novo nao abriu neste computador.' + #13#10 +
    'O guardian antigo (se havia) continua funcionando.' + #13#10#13#10 +
    'Veja o guardian.log na pasta ' + PastaApp, mbError, MB_OK, IDOK);
end;

{ Recebe a saida do guardian.py --parear linha a linha (vai tambem para o log de
  instalacao; o --parear nunca escreve o codigo nem o token). }
procedure LerSaidaParear(const S: String; const Error, FirstLine: Boolean);
begin
  Log('guardian.py --parear: ' + S);
  if Error or (Trim(S) = '') then
    Exit;
  if Copy(S, 1, 7) = 'PCNOME=' then
    PcNomePainel := Trim(Copy(S, 8, Length(S)))
  else if (MensagemParear = '') and (Copy(S, 1, 10) <> 'RESULTADO=') then
    MensagemParear := Trim(S);
end;

{ Roda "guardian.py --parear CODIGO" com o Python embutido (o Inno e o PowerShell do
  Windows 7 nao garantem TLS 1.2). Devolve o codigo de saida dele (0 ok, 2 formato,
  3 codigo invalido, 4 rede, 5 resposta, 6 gravacao, 7 URL) ou -1 se nem rodou. }
function RodarParear(const Codigo: String): Integer;
var
  Saida: Integer;
begin
  MensagemParear := '';
  PcNomePainel := '';
  Log('Conectando ao painel (guardian.py --parear).');
  try
    if not ExecAndLogOutput(PastaApp + '\python\python.exe',
                            '"' + PastaApp + '\guardian.py" --parear ' + Codigo,
                            PastaApp, SW_HIDE, ewWaitUntilTerminated, Saida, @LerSaidaParear) then
    begin
      Log('ERRO ao rodar o Python embutido: ' + SysErrorMessage(Saida));
      Saida := -1;
    end;
  except
    Log('ERRO ao rodar o Python embutido: ' + GetExceptionMessage);
    Saida := -1;
  end;
  Log(Format('guardian.py --parear -> %d', [Saida]));
  Result := Saida;
end;

{ Janela para digitar o codigo de novo (o anterior nao deu certo). False = Pular. }
function PedirOutroCodigo(const Problema: String; var Codigo: String): Boolean;
var
  Form: TSetupForm;
  Texto: TNewStaticText;
  Edit: TNewEdit;
  Conectar, Pular: TNewButton;
  W: Integer;
begin
  Form := CreateCustomForm(ScaleX(420), ScaleY(200), True, True);
  try
    Form.Caption := 'Conectar ao painel Música GEF';

    Texto := TNewStaticText.Create(Form);
    Texto.AutoSize := False;
    Texto.WordWrap := True;
    Texto.Left := ScaleX(10);
    Texto.Top := ScaleY(10);
    Texto.Width := Form.ClientWidth - ScaleX(20);
    Texto.Caption := Problema + #13#10#13#10 +
      'Digite o código de 6 dígitos e clique em Conectar. Para terminar a instalação sem ' +
      'conectar, clique em Pular (dá para conectar depois: menu Iniciar > Spotify Guardian > ' +
      'Conectar ao painel).';
    Texto.Parent := Form;
    Texto.AdjustHeight;

    Edit := TNewEdit.Create(Form);
    Edit.Left := ScaleX(10);
    Edit.Top := Texto.Top + Texto.Height + ScaleY(12);
    Edit.Width := ScaleX(120);
    Edit.Text := Codigo;
    Edit.Parent := Form;

    Form.ClientHeight := Edit.Top + Edit.Height + ScaleY(16 + 23 + 10);

    Conectar := TNewButton.Create(Form);
    Conectar.Caption := 'Conectar';
    Conectar.ModalResult := mrOk;
    Conectar.Default := True;
    Conectar.Parent := Form;

    Pular := TNewButton.Create(Form);
    Pular.Caption := 'Pular';
    Pular.ModalResult := mrCancel;
    Pular.Cancel := True;
    Pular.Parent := Form;

    W := Form.CalculateButtonWidth([Conectar.Caption, Pular.Caption]);
    Conectar.Width := W;
    Pular.Width := W;
    Conectar.Height := ScaleY(23);
    Pular.Height := ScaleY(23);
    Pular.Left := Form.ClientWidth - ScaleX(10) - W;
    Conectar.Left := Pular.Left - ScaleX(6) - W;
    Conectar.Top := Form.ClientHeight - ScaleY(23 + 10);
    Pular.Top := Conectar.Top;

    Form.ActiveControl := Edit;
    Form.FlipAndCenterIfNeeded(True, WizardForm, False);
    Result := Form.ShowModal = mrOk;
    if Result then
      Codigo := Trim(Edit.Text);
  finally
    Form.Free;
  end;
end;

{ Pareia o PC com o painel. Codigo errado ou sem rede: deixa digitar de novo ou
  pular. Instalacao silenciosa nunca abre janela: sem rede tenta 3 vezes, o resto
  desiste (codigo de saida 8). Devolve False so se o Python embutido nem rodou:
  ai o guardian novo tambem nao abriria. }
function Parear(Codigo: String): Boolean;
var
  Saida, Tentativas: Integer;
  Problema: String;
begin
  Result := True;
  Tentativas := 0;
  while True do
  begin
    Tentativas := Tentativas + 1;
    if LimparCodigo(Codigo) = '' then
    begin
      Log('Codigo do painel com formato invalido (precisa ter 6 digitos).');
      MensagemParear := '';
      Saida := 2;
    end
    else
      Saida := RodarParear(LimparCodigo(Codigo));
    case Saida of
      0: begin
           Pareou := True;
           Exit;
         end;
      2: Problema := 'O código do painel tem 6 dígitos (só números).';
      3: Problema := 'Código inválido, vencido ou já usado. Gere um código novo na página deste PC no painel.';
      4: begin
           Problema := 'Sem conexão com o painel Música GEF. Confira a internet deste computador e tente de novo.';
           if MensagemParear <> '' then
             Problema := Problema + #13#10#13#10 + 'Detalhe: ' + MensagemParear;
         end;
      5, 6, 7:
         begin
           PareamentoFalhou := True;
           if not WizardSilent then
             MsgBox('Não foi possível conectar este PC ao painel Música GEF:' + #13#10 + MensagemParear + #13#10#13#10 +
                    'A instalação continua sem o painel. Para conectar depois: menu Iniciar > ' +
                    'Spotify Guardian > Conectar ao painel.', mbError, MB_OK);
           Exit;
         end;
    else
      begin
        { nem o Python rodou (DLL faltando, biblioteca quebrada...) }
        PareamentoFalhou := True;
        Result := False;
        Exit;
      end;
    end;
    if WizardSilent then
    begin
      if (Saida <> 4) or (Tentativas >= 3) then
      begin
        Log('Pareamento com o painel nao foi feito.');
        PareamentoFalhou := True;
        Exit;
      end;
      Sleep(10000);
    end
    else
    begin
      if Saida <> 4 then
        Codigo := '';   { sem rede: deixa o mesmo codigo, e so clicar em Conectar de novo }
      if not PedirOutroCodigo(Problema, Codigo) then
      begin
        Log('Pareamento com o painel pulado.');
        PareamentoFalhou := True;
        Exit;
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Config: String;
  JaTinhaAtalhos: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    if not TrocarPython then
    begin
      if TinhaPython then
      begin
        VoltouVersao := True;
        Exit;  { InstalouOk continua False: o DeinitializeSetup restaura a versao anterior }
      end;
      { 1a instalacao: nada foi migrado ainda; um guardian antigo continua como estava }
      NaoAbriu := True;
      ConfirmarInstalacao;
      SuppressibleMsgBox('Nao foi possivel preparar o Python do Spotify Guardian (arquivos em uso).' + #13#10 +
        'Reinicie o computador e rode o instalador de novo.', mbError, MB_OK, IDOK);
      Exit;
    end;
    Config := PastaApp + '\config.json';
    JaTinhaAtalhos := TemAtalhosGuardados;
    RodarPS(PastaApp + '\migrar.ps1',
            ParamsMigrar(PastaApp, 'Migrar') + ' -Estado "' + ArquivoEstado + '"');
    if not FileExists(Config) and SemPainel and (PaginaCred <> nil) and EhHex32(Trim(PaginaCred.Values[0])) then
    begin
      if SaveStringToFile(Config, MontarConfig, False) then
        Log('config.json criado com os dados informados.')
      else
        Log('ERRO ao gravar config.json.');
    end;
    { pareia so agora: com o Python novo no lugar e o config.json antigo ja migrado }
    if CodigoPedido <> '' then
      if not Parear(CodigoPedido) then
      begin
        Log('O Python novo nao rodou o guardian.py --parear.');
        NovoNaoAbriu(JaTinhaAtalhos);
        Exit;
      end;
    if not TemConfiguracao then
    begin
      Log('ATENCAO: instalado sem config.json e sem painel.json; guardian nao iniciado.');
      ConfirmarInstalacao;
      Exit;
    end;
    IniciarGuardian;
    { So descarta o que funcionava (versao anterior ou guardian antigo de outra
      pasta) depois que o novo abriu mesmo. Senao a loja ficaria sem guardian. }
    if DeveIniciar and not GuardianSubiu(EsperaGuardian) then
    begin
      Log('O guardian novo nao abriu a tempo.');
      NovoNaoAbriu(JaTinhaAtalhos);
      Exit;
    end;
    { o novo abriu: agora sim para os guardians antigos (de outras pastas) }
    RodarPS(PastaApp + '\parar.ps1', '-Pasta "' + PastaApp + '" -SoAntigos');
    ConfirmarInstalacao;
    DesativarAntigoDeTodosOsUsuarios;
  end;
end;

{ Desinstalacao: a tarefa sai ANTES de o guardian ser fechado, senao ela reabriria ele. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoverTarefaVigia;
end;

procedure DeinitializeSetup;
var
  Descartado: String;
begin
  { A instalacao parou no meio depois de fechar o guardian: devolve o Python
    antigo e reabre o guardian, para a loja nao ficar sem ele. }
  if GuardianParado and not InstalouOk then
  begin
    Log('Instalacao nao terminou: restaurando o guardian anterior.');
    if DirExists(PastaApp + '\python.novo') then
      DelTree(PastaApp + '\python.novo', True, True, True);
    if PythonGuardado <> '' then
    begin
      { o Python novo sai do caminho com um RENOMEIO e so e apagado depois que o
        anterior voltou: apagar primeiro podia deixar a pasta "python" pela metade
        (arquivo em uso) e ai o anterior nao tinha como voltar para o lugar }
      Descartado := '';
      if DirExists(PastaApp + '\python') then
      begin
        Descartado := PastaApp + '\python.anterior-falhou-' + GetDateTimeString('yyyymmddhhnnss', #0, #0);
        if not RenomearPasta(PastaApp + '\python', Descartado) then
        begin
          Log('ERRO ao tirar o Python novo do caminho; o anterior continua em ' + PythonGuardado);
          Descartado := '';
        end;
      end;
      if not DirExists(PastaApp + '\python') then
        if not RenomearPasta(PythonGuardado, PastaApp + '\python') then
          Log('ERRO ao restaurar o Python anterior de ' + PythonGuardado);
      if Descartado <> '' then
        if not DelTree(Descartado, True, True, True) then
          Log('Python descartado ainda em uso, fica para a proxima instalacao apagar.');
    end;
    CopiasAnteriores(1);
    { o Windows ainda mostraria a versao nova em "Aplicativos instalados" }
    { a versao que voltou pode nao conhecer a tarefa agendada: tira (a nova recria sozinha) }
    if VoltouVersao then
      RemoverTarefaVigia;
    if VoltouVersao and (VersaoAnterior <> '') then
    begin
      RegWriteStringValue(HKCU, ChaveDesinstalar, 'DisplayVersion', VersaoAnterior);
      Log('Versao registrada de volta para ' + VersaoAnterior);
    end;
    IniciarGuardian;
  end;
end;

{ Codigo de saida 9 = instalou, mas o guardian novo nao abriu e a versao anterior
  voltou (fica claro no atualizacao.log e para quem roda o instalador em lote).
  Codigo de saida 8 = instalou, mas o pareamento com o painel pedido (codigo na
  pagina ou /CODIGO=) nao deu certo. }
function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if VoltouVersao or NaoAbriu then
    Result := 9
  else if PareamentoFalhou then
    Result := 8;
end;

{ O guardian vai rodar no modo antigo (login do Spotify no navegador deste PC)?
  Igual ao decidir_modo do guardian.py: nunca pareado e config.json com client_id.
  O Bloco de Notas pode salvar o config.json em "Unicode" (UTF-16): ai cada letra
  vem seguida (ou precedida) de um byte 0. }
function ModoLegado: Boolean;
var
  Texto: AnsiString;
begin
  Result := False;
  if FileExists(ExpandConstant('{app}\painel.json')) or FileExists(ExpandConstant('{app}\painel.json.revogado')) then
    Exit;
  if LoadStringFromFile(ExpandConstant('{app}\config.json'), Texto) then
    Result := (Pos('client_id', Texto) > 0) or
              (Pos('c'#0'l'#0'i'#0'e'#0'n'#0't'#0'_'#0'i'#0'd'#0, Texto) > 0);
end;

{ Frase da tela final sobre o painel ('' no modo antigo). }
function SituacaoPainel: String;
begin
  Result := '';
  if Pareou then
  begin
    Result := 'Este PC foi conectado ao painel Música GEF';
    if PcNomePainel <> '' then
      Result := Result + ' como "' + PcNomePainel + '"';
    Result := Result + '.';
  end
  else if PareamentoFalhou then
    Result := 'ATENÇÃO: este PC NÃO foi conectado ao painel Música GEF. Para conectar, gere um ' +
              'código no painel e use menu Iniciar > Spotify Guardian > Conectar ao painel.'
  else if FileExists(ExpandConstant('{app}\painel.json')) then
    Result := 'Este PC está conectado ao painel Música GEF.'
  else if not ModoLegado then
    Result := 'Este PC ainda não está conectado ao painel Música GEF: o Spotify Guardian só cuida ' +
              'da música depois disso (menu Iniciar > Spotify Guardian > Conectar ao painel).';
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Extra: String;
begin
  if (PaginaPainel <> nil) and (CurPageID = PaginaPainel.ID) then
  begin
    if PcDesconectado then
      AvisoPainel.Caption := 'Este PC foi desconectado do painel. Digite um código novo para conectá-lo de novo.'
    else if ConfigExiste then
      AvisoPainel.Caption := 'Este computador já tem uma configuração do Spotify Guardian (modo antigo, ' +
        'sem o painel). Deixe o código em branco para continuar como está.'
    else
      AvisoPainel.Caption := '';
    AvisoPainel.AdjustHeight;
    AtualizarPaginaPainel(nil);
  end;
  if (PaginaCred <> nil) and (CurPageID = PaginaCred.ID) then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  if (CurPageID = wpFinished) and VoltouVersao then
  begin
    { atualizacao feita na mao que nao deu certo: a tela final nao pode dizer "concluido" }
    WizardForm.FinishedHeadingLabel.Caption := 'A atualizacao NAO foi aplicada';
    WizardForm.FinishedLabel.Caption :=
      'Nao foi possivel atualizar o Spotify Guardian neste computador ' +
      '(arquivos em uso, ou o guardian novo nao abriu).' + #13#10#13#10 +
      'A versao anterior volta a funcionar quando esta janela for fechada.' + #13#10#13#10 +
      'Reinicie o computador e rode o instalador de novo.';
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  end
  else if CurPageID = wpFinished then
  begin
    Extra := SituacaoPainel;
    { so no modo antigo: no modo painel o login do Spotify e feito no painel }
    if GuardianIniciado and ModoLegado and not FileExists(ExpandConstant('{app}\.cache_gef')) then
    begin
      if Extra <> '' then
        Extra := Extra + #13#10#13#10;
      Extra := Extra + 'PRIMEIRO USO: o navegador vai abrir para o login do Spotify. ' +
        'Entre com a conta Spotify DESTE posto e clique em Concordo/Autorizar.';
    end;
    if Extra <> '' then
    begin
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + Extra;
      WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    end;
  end;
end;
