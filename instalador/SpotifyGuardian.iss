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
;   - Em maquina nova, pede client_id / client_secret / playlist.
;   - Coloca o guardian na inicializacao do Windows e ja deixa rodando.
;   - Se o guardian antigo estiver na inicializacao de TODOS os usuarios, pede o
;     aceite de administrador (UAC) so para tirar esse atalho. O resto nao precisa.
; Parametros extras (usados nos testes): /SEMINICIAR (nao abre o guardian no fim) e
; /PASTACOMUM=pasta (pasta "Inicializar de todos os usuarios" de mentira, sem UAC).

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

[Files]
; primeiro: copias usadas pelo proprio instalador antes de instalar (abrem rapido,
; sem precisar descompactar os 2 Pythons antes)
Source: "parar.ps1"; Flags: dontcopy
Source: "migrar.ps1"; Flags: dontcopy
; o Python vai para uma pasta nova (o antigo foi guardado de lado no PrepareToInstall)
Source: "{#Build}\python38\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: UsarPython38
Source: "{#Build}\python314\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: not UsarPython38
Source: "parar.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "migrar.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Build}\app\diagnostico.py"; DestDir: "{app}"; Flags: ignoreversion
; por ultimo: se algo falhar antes, o guardian.py antigo continua junto do Python antigo
Source: "{#Build}\app\guardian.py"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userstartup}\Spotify Guardian GEF"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\guardian.py"""; WorkingDir: "{app}"; Comment: "Mantem a playlist do GEF tocando"
Name: "{group}\Ver log do Spotify Guardian"; Filename: "{win}\notepad.exe"; Parameters: """{app}\guardian.log"""
Name: "{group}\Diagnostico do Spotify"; Filename: "{app}\python\python.exe"; Parameters: """{app}\diagnostico.py"""; WorkingDir: "{app}"
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
; config.json, .cache_gef (login) e os logs ficam, para uma reinstalacao
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\python.anterior*"
Type: files; Name: "{app}\*.anterior"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
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
  NaoAbriu: Boolean;           { 1a instalacao: o guardian novo nao abriu; o antigo foi mantido }

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

procedure IniciarGuardian;
var
  Codigo: Integer;
begin
  if not DeveIniciar then
    Exit;
  if not FileExists(PastaApp + '\config.json') then
  begin
    Log('Guardian nao iniciado: falta config.json.');
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

{ Ja existe config.json na pasta nova ou numa instalacao antiga? }
function ConfigExiste: Boolean;
var
  Estado: AnsiString;
begin
  if not JaDetectou then
  begin
    JaDetectou := True;
    AchouConfig := FileExists(AddBackslash(WizardDirValue) + 'config.json');
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

procedure InitializeWizard;
begin
  PaginaCred := CreateInputQueryPage(wpSelectDir,
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
  if (PaginaCred <> nil) and (PageID = PaginaCred.ID) then
    Result := ConfigExiste;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
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
  CopiasAnteriores(0);
  { Guarda o Python atual de lado em vez de apagar: da para renomear a pasta mesmo
    com algo rodando dela (ex.: janela do Diagnostico). Se a instalacao falhar,
    o DeinitializeSetup devolve ele e reabre o guardian. }
  if DirExists(PastaApp + '\python') then
  begin
    PythonGuardado := PastaApp + '\python.anterior-' + GetDateTimeString('yyyymmddhhnnss', #0, #0);
    if RenameFile(PastaApp + '\python', PythonGuardado) then
      Log('Python anterior guardado em ' + PythonGuardado)
    else
    begin
      Log('ERRO ao guardar o Python anterior.');
      PythonGuardado := '';
      Result := 'Nao foi possivel substituir o Python do Spotify Guardian (arquivos em uso). ' +
                'Feche as janelas do Spotify Guardian (ex.: Diagnostico) e tente de novo.';
    end;
  end;
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

procedure CurStepChanged(CurStep: TSetupStep);
var
  Config: String;
begin
  if CurStep = ssPostInstall then
  begin
    Config := PastaApp + '\config.json';
    RodarPS(PastaApp + '\migrar.ps1',
            ParamsMigrar(PastaApp, 'Migrar') + ' -Estado "' + ArquivoEstado + '"');
    if not FileExists(Config) and (PaginaCred <> nil) and EhHex32(Trim(PaginaCred.Values[0])) then
    begin
      if SaveStringToFile(Config, MontarConfig, False) then
        Log('config.json criado com os dados informados.')
      else
        Log('ERRO ao gravar config.json.');
    end;
    if not FileExists(Config) then
    begin
      Log('ATENCAO: instalado sem config.json; guardian nao iniciado.');
      ConfirmarInstalacao;
      Exit;
    end;
    IniciarGuardian;
    { So descarta o que funcionava (versao anterior ou guardian antigo de outra
      pasta) depois que o novo abriu mesmo. Senao a loja ficaria sem guardian. }
    if DeveIniciar and not GuardianSubiu(240) then
    begin
      RodarPS(PastaApp + '\parar.ps1', '-Pasta "' + PastaApp + '" -SoPasta');
      if PythonGuardado <> '' then
      begin
        Log('O guardian novo nao abriu em 240 s: voltando para a versao anterior.');
        VoltouVersao := True;
        Exit;  { InstalouOk continua False: o DeinitializeSetup restaura a versao anterior }
      end;
      { 1a instalacao: o guardian antigo nem foi parado; religa os atalhos dele }
      Log('O guardian novo nao abriu em 240 s: mantendo o guardian antigo.');
      RodarPS(PastaApp + '\migrar.ps1', ParamsMigrar(PastaApp, 'Desfazer'));
      NaoAbriu := True;
      GuardianIniciado := False;
      ConfirmarInstalacao;
      SuppressibleMsgBox('O Spotify Guardian novo nao abriu neste computador.' + #13#10 +
        'O guardian antigo (se havia) continua funcionando.' + #13#10#13#10 +
        'Veja o guardian.log na pasta ' + PastaApp, mbError, MB_OK, IDOK);
      Exit;
    end;
    { o novo abriu: agora sim para os guardians antigos (de outras pastas) }
    RodarPS(PastaApp + '\parar.ps1', '-Pasta "' + PastaApp + '" -SoAntigos');
    ConfirmarInstalacao;
    DesativarAntigoDeTodosOsUsuarios;
  end;
end;

procedure DeinitializeSetup;
begin
  { A instalacao parou no meio depois de fechar o guardian: devolve o Python
    antigo e reabre o guardian, para a loja nao ficar sem ele. }
  if GuardianParado and not InstalouOk then
  begin
    Log('Instalacao nao terminou: restaurando o guardian anterior.');
    if PythonGuardado <> '' then
    begin
      { o Python novo (inteiro ou pela metade) sai do caminho; se algo ainda
        prende arquivos dele, e so renomeado (e apagado numa proxima instalacao) }
      if DirExists(PastaApp + '\python') then
        if not DelTree(PastaApp + '\python', True, True, True) then
          RenameFile(PastaApp + '\python',
                     PastaApp + '\python.anterior-falhou-' + GetDateTimeString('yyyymmddhhnnss', #0, #0));
      if not RenameFile(PythonGuardado, PastaApp + '\python') then
        Log('ERRO ao restaurar o Python anterior de ' + PythonGuardado);
    end;
    CopiasAnteriores(1);
    { o Windows ainda mostraria a versao nova em "Aplicativos instalados" }
    if VoltouVersao and (VersaoAnterior <> '') then
    begin
      RegWriteStringValue(HKCU, ChaveDesinstalar, 'DisplayVersion', VersaoAnterior);
      Log('Versao registrada de volta para ' + VersaoAnterior);
    end;
    IniciarGuardian;
  end;
end;

{ Codigo de saida 9 = instalou, mas o guardian novo nao abriu e a versao anterior
  voltou (fica claro no atualizacao.log e para quem roda o instalador em lote). }
function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if VoltouVersao or NaoAbriu then
    Result := 9;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (PaginaCred <> nil) and (CurPageID = PaginaCred.ID) then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  if (CurPageID = wpFinished) and GuardianIniciado and not FileExists(ExpandConstant('{app}\.cache_gef')) then
  begin
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      'PRIMEIRO USO: o navegador vai abrir para o login do Spotify. ' +
      'Entre com a conta Spotify DESTE posto e clique em Concordo/Autorizar.';
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  end;
end;
