"""
Spotify Guardian GEF
--------------------
Mantem a PLAYLIST DO GEF sempre tocando na maquina da loja (Windows 7 / 10).
  - Reabre o Spotify se ele cair                       (tasklist)
  - Fecha o popup de "versao desatualizada" do Win7    (ctypes, nativo)
  - Via API: checa se esta tocando e, se nao, dispara
    a PLAYLIST DO GEF -> forca a playlist certa, nao so um "play"
  - Roda em loop, com log e janela de horario

  - Se atualiza sozinho 1x por dia pelo GitHub Releases

Requisitos: conta Spotify PREMIUM + app no Spotify for Developers.
Instalado pelo SpotifyGuardian-Setup.exe, que ja traz o Python junto
(3.8 no Windows 7, 3.14 no Windows 10/11).

Uso:
  guardian.py                 roda o guardian (e o que a inicializacao do Windows chama)
  guardian.py --sem-espera    idem, sem o startup_delay
  guardian.py --vigia         chamado pela tarefa agendada a cada 5 min: so abre o
                              guardian se ele nao estiver rodando
  guardian.py --relogin       refaz o login do Spotify (apaga o .cache_gef)
  guardian.py --reiniciar     reinicia o guardian (ex.: depois de editar o config.json)
"""

import os
import re
import sys
import time
import json
import ctypes
import socket
import random
import signal
import hashlib
import logging
import tempfile
import threading
import subprocess
from ctypes import wintypes
from datetime import datetime

MUTEX_NOME = "Local\\SpotifyGuardianGEF"
MUTEX_INSTALADOR = "SpotifyGuardianSetupGEF"  # SetupMutex do instalador (Inno Setup)
# existe enquanto a janela "Refazer login" / "Reiniciar" trabalha: ela fecha o guardian de proposito
MUTEX_AJUDANTE = "Local\\SpotifyGuardianGEFAjudante"


def _mutex_existe(nome):
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenMutexW.restype = wintypes.HANDLE
        k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenMutexW(0x00100000, False, nome)  # SYNCHRONIZE
        if h:
            k32.CloseHandle(h)
            return True
        return ctypes.get_last_error() == 5  # existe, mas e de um processo de administrador
    except Exception:
        pass
    return False


# A tarefa agendada chama "guardian.py --vigia" a cada 5 minutos. Se o guardian ja
# esta rodando, se o instalador esta no meio de uma instalacao/atualizacao ou se a
# janela "Refazer login"/"Reiniciar" esta trabalhando, sai aqui mesmo: antes de
# carregar as bibliotecas pesadas e sem escrever no log.
if __name__ == "__main__" and "--vigia" in sys.argv:
    if (_mutex_existe(MUTEX_NOME) or _mutex_existe(MUTEX_INSTALADOR)
            or _mutex_existe(MUTEX_AJUDANTE)):
        sys.exit(0)

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

VERSAO = "2.0.2"
# "dono/repositorio" no GitHub de onde vem as atualizacoes. O build.ps1 preenche.
REPO_ATUALIZACAO = ""

# Corrige erro quando o NOME DO COMPUTADOR tem acento (ex.: "ESTACAO-RECEPCAO"),
# que quebra o servidor local usado no login do Spotify no Windows.
_getfqdn_orig = socket.getfqdn
def _getfqdn_seguro(nome=""):
    try:
        return _getfqdn_orig(nome)
    except Exception:
        return "localhost"
socket.getfqdn = _getfqdn_seguro

# O spotipy escreve "HTTP Error for PUT ..." direto na tela (stderr).
# O guardian ja registra esses erros no log com mais detalhe, entao silencia.
logging.getLogger("spotipy").addHandler(logging.NullHandler())

# Acha a pasta certa tanto rodando como .py quanto como .exe (PyInstaller).
# Assim o config.json, o .cache_gef e o log ficam na MESMA pasta do executavel.
if getattr(sys, "frozen", False):
    PASTA = os.path.dirname(sys.executable)   # rodando como .exe
else:
    PASTA = os.path.dirname(os.path.abspath(__file__))  # rodando como .py

LOG_FILE = os.path.join(PASTA, "guardian.log")

# Console com pagina de codigo antiga (cp850): caractere que nao existe nela
# (emoji no nome da conta/aparelho) vira "?" em vez de derrubar o programa.
for _saida in (sys.stdout, sys.stderr):
    try:
        _saida.reconfigure(errors="replace")
    except Exception:
        pass


def log(msg):
    linha = f"[{datetime.now():%d/%m/%Y %H:%M:%S}] {msg}"
    try:
        print(linha, flush=True)
    except Exception:
        pass  # console fechado/sem suporte: o que vale e o arquivo
    try:
        # passou de 2 MB: guarda o antigo como guardian.log.1 e comeca outro
        if os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def eh_windows7():
    try:
        return sys.getwindowsversion()[:2] == (6, 1)
    except Exception:
        return False


def normalizar_playlist(uri):
    # Aceita o link copiado do app ("https://open.spotify.com/playlist/ID?si=..."),
    # a URI com "?si=..." no fim ou so o ID, e devolve "spotify:playlist:ID".
    # Com o "?si=" a API nao reconhece a playlist.
    u = (uri or "").strip().split("?")[0].strip().rstrip("/")
    if "open.spotify.com" in u and "/playlist/" in u:
        u = "spotify:playlist:" + u.split("/")[-1]
    elif re.match(r"^[0-9A-Za-z]{22}$", u):
        u = "spotify:playlist:" + u
    return u


def ler_config(caminho):
    # O Bloco de Notas do Win7 salva em "ANSI" ou em UTF-8 COM BOM;
    # o do Win10 em UTF-8 sem BOM. Aceita todos.
    with open(caminho, "rb") as f:
        bruto = f.read()
    if bruto[:2] in (b"\xff\xfe", b"\xfe\xff"):
        texto = bruto.decode("utf-16")                        # "Unicode"
    else:
        try:
            texto = bruto.decode("utf-8-sig")                 # UTF-8 com/sem BOM
        except UnicodeDecodeError:
            texto = bruto.decode("cp1252", errors="replace")  # "ANSI"
    return json.loads(texto)


# ---------- carrega config.json ----------
try:
    CFG = ler_config(os.path.join(PASTA, "config.json"))

    CLIENT_ID      = CFG["client_id"]
    CLIENT_SECRET  = CFG["client_secret"]
    REDIRECT_URI   = CFG["redirect_uri"]
    PLAYLIST_CFG   = CFG["playlist_uri"]
    PLAYLIST_URI   = normalizar_playlist(PLAYLIST_CFG)
    EMBARALHAR     = CFG.get("embaralhar", True)
    INTERVALO      = max(int(CFG.get("check_interval", 180)), 60)  # nunca menos que 60s
    # numeros escritos como texto ("8") tambem valem; lixo da erro claro aqui
    STARTUP_DELAY  = float(CFG.get("startup_delay") or 0)
    HORARIO_INICIO = CFG.get("horario_inicio", None)
    HORARIO_FIM    = CFG.get("horario_fim", None)
    if HORARIO_INICIO is not None:
        HORARIO_INICIO = int(HORARIO_INICIO)
    if HORARIO_FIM is not None:
        HORARIO_FIM = int(HORARIO_FIM)
    # Nome do aparelho do PC da loja como aparece no Spotify Connect (opcional).
    # Se vazio, usa o nome do computador.
    DISPOSITIVO    = (CFG.get("dispositivo") or "").strip()
    # O popup de "versao desatualizada" so existe no Windows 7. No Windows 10
    # nao ha popup pra fechar.
    FECHAR_POPUP   = CFG.get("fechar_popup", eh_windows7())
    # Atualizacao automatica pelo GitHub Releases (1x por dia).
    ATUALIZACAO_AUTO   = CFG.get("atualizacao_automatica", True)
    URL_ATUALIZACAO    = (CFG.get("url_atualizacao") or
                          (f"https://api.github.com/repos/{REPO_ATUALIZACAO}/releases/latest"
                           if REPO_ATUALIZACAO else ""))
    ATUALIZACAO_ATRASO = int(CFG.get("atualizacao_atraso_s", 600))  # 1a checagem apos iniciar
    # Tarefa agendada do Windows que reabre o guardian se ele for fechado.
    REABRIR_SOZINHO    = CFG.get("reabrir_sozinho", True)
except Exception as e:
    _erro_cfg = f"ERRO no config.json: {type(e).__name__}: {e}"
    if __name__ == "__main__" and "--vigia" in sys.argv:
        # a tarefa agendada tenta de novo a cada 5 minutos: anota o erro uma vez so
        try:
            with open(LOG_FILE, "rb") as _f:
                _f.seek(max(0, os.path.getsize(LOG_FILE) - 600))
                _ja_anotou = _erro_cfg.encode("utf-8") in _f.read()
        except OSError:
            _ja_anotou = False
        if not _ja_anotou:
            log(_erro_cfg)
        sys.exit(0)
    log(_erro_cfg)
    if __name__ == "__main__" and ("--relogin" in sys.argv or "--reiniciar" in sys.argv):
        # atalhos do menu Iniciar abrem janela preta: mostra o erro antes de ela fechar
        print(f"\nERRO no config.json: {type(e).__name__}: {e}")
        print("Corrija o arquivo (menu Iniciar > Spotify Guardian > Editar configuracao).")
        input("\nEnter pra fechar...")
    raise

PLAYLIST_TOTAL = None  # tamanho da playlist (buscado 1x e guardado)

SCOPE = "user-read-playback-state user-modify-playback-state"
CREATE_NO_WINDOW = 0x08000000  # evita piscar janela preta no modo .exe
CACHE = os.path.join(PASTA, ".cache_gef")
SPOTIFY_EXE = os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe")
# Spotify instalado pela Microsoft Store (comum no Windows 10)
SPOTIFY_EXE_LOJA = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe")

# (conexao, resposta) em segundos. Vale tambem para a renovacao do login,
# que antes nao tinha limite nenhum.
TIMEOUT = (10, 20)
ESPERA_FALHA_REDE = 20   # apos falha de rede, tenta de novo logo...
MAX_FALHAS_RAPIDAS = 3   # ...ate 3 vezes seguidas; depois volta ao intervalo normal

ASSET_SETUP = "SpotifyGuardian-Setup.exe"   # nome do instalador no GitHub Releases
INTERVALO_ATUALIZACAO = 24 * 3600
ESTADO_ATUALIZACAO = os.path.join(PASTA, "atualizacao_estado.json")
MAX_TENTATIVAS_ATUALIZACAO = 2   # o mesmo instalador roda no maximo 2x (1 por dia)
PRAZO_DOWNLOAD = 15 * 60         # desiste do download depois de 15 min
PRAZO_LOGIN = 10 * 60            # espera o login no navegador por ate 10 min
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
SCHTASKS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe")
# uma tarefa por usuario do Windows (o nome da tarefa vale para o PC inteiro)
NOME_TAREFA = "Spotify Guardian GEF (" + os.environ.get("USERNAME", "usuario") + ")"
INTERVALO_VIGIA_MIN = 5
ARQ_TAREFA = os.path.join(PASTA, "tarefa_vigia.txt")  # marca da tarefa ja criada
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def resumo_erro(e):
    # Erro do Spotify em uma linha so, com status, mensagem e motivo.
    if isinstance(e, spotipy.SpotifyException):
        msg = " ".join(str(getattr(e, "msg", "")).split())
        return f"HTTP {e.http_status} - {msg} (motivo: {getattr(e, 'reason', None)})"
    return f"{type(e).__name__}: {' '.join(str(e).split())}"


def versao(pacote):
    try:
        from importlib.metadata import version
        return version(pacote)
    except Exception:
        # no .exe do PyInstaller os metadados nao vem junto
        return getattr(sys.modules.get(pacote), "__version__", "?")


def desligar_quickedit():
    # Rodando em janela preta (python.exe), um clique dentro dela liga a
    # "Edicao Rapida" do Windows e CONGELA o programa ate apertarem Esc.
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.GetStdHandle.argtypes = [wintypes.DWORD]
        k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        entrada = k32.GetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF))  # STD_INPUT_HANDLE
        modo = wintypes.DWORD()
        if entrada and k32.GetConsoleMode(entrada, ctypes.byref(modo)):
            # liga ENABLE_EXTENDED_FLAGS (0x80) e desliga ENABLE_QUICK_EDIT_MODE (0x40)
            k32.SetConsoleMode(entrada, (modo.value | 0x0080) & ~0x0040)
    except Exception:
        pass


def _instalar_prazo_no_login():
    # No 1o login o spotipy abre o navegador e espera a resposta num servidor
    # local (127.0.0.1:8888) SEM prazo: se ninguem logar, o guardian trava ali
    # para sempre. Aqui ele desiste depois de PRAZO_LOGIN e fecha a porta.
    try:
        import spotipy.oauth2 as oauth2
        original = oauth2.start_local_http_server
    except Exception:
        return

    def com_prazo(port, handler=oauth2.RequestHandler):
        class ComPrazo(handler):
            timeout = 60  # conexao aberta que nao manda nada nao trava o login

        servidor = original(port, ComPrazo)
        servidor.timeout = PRAZO_LOGIN
        atender = servidor.handle_request

        def atender_e_fechar():
            try:
                atender()
            finally:
                servidor.server_close()
        servidor.handle_request = atender_e_fechar
        return servidor
    oauth2.start_local_http_server = com_prazo


_instalar_prazo_no_login()


def login_pendente(e):
    # SpotifyOauthError de quando ninguem terminou o login no navegador a tempo
    # (ou cancelou na pagina do Spotify)
    texto = str(e)
    return ("has not been accessed" in texto or "access_denied" in texto
            or "Received error from" in texto)


def conectar(cache_path=None):
    # requests_session=False: cada chamada abre uma conexao NOVA com o Spotify.
    # Antes a conexao ficava parada entre uma chamada e outra (1 hora, no caso da
    # renovacao do login) e o roteador/firewall derrubava ela sem avisar. Na
    # proxima chamada vinha "Read timed out" ou "Connection aborted".
    auth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=cache_path or CACHE,
        open_browser=True,
        requests_timeout=TIMEOUT,
        requests_session=False,
    )
    return spotipy.Spotify(
        auth_manager=auth,
        requests_timeout=TIMEOUT,
        requests_session=False,
    )


def dentro_do_horario():
    if HORARIO_INICIO is None or HORARIO_FIM is None or HORARIO_INICIO == HORARIO_FIM:
        return True  # sem horario (ou inicio == fim): toca 24h
    hora = datetime.now().hour
    if HORARIO_INICIO <= HORARIO_FIM:
        return HORARIO_INICIO <= hora < HORARIO_FIM
    return hora >= HORARIO_INICIO or hora < HORARIO_FIM  # ex.: das 18h as 2h


_AVISOU_TASKLIST = False


def spotify_aberto():
    # Usa o tasklist do proprio Windows (nao precisa de biblioteca externa).
    # O tasklist depende do WMI; se ele falhar (WMI corrompido/travado),
    # confere pelas janelas. Devolve None so se nada deu pra saber.
    global _AVISOU_TASKLIST
    try:
        saida = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        return b"spotify.exe" in saida.lower()
    except Exception as e:
        if not _AVISOU_TASKLIST:
            _AVISOU_TASKLIST = True
            log(f"tasklist falhou ({resumo_erro(e)}). Conferindo o Spotify pelas janelas abertas.")
        return _spotify_tem_janela()


def _spotify_tem_janela():
    # Plano B sem WMI: procura alguma janela (mesmo escondida na bandeja)
    # que pertenca a um Spotify.exe.
    achou = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if _nome_processo(hwnd).lower() == "spotify.exe":
            achou.append(hwnd)
            return False  # achou, para de procurar
        return True

    try:
        ctypes.windll.user32.EnumWindows(EnumProc(callback), 0)
    except Exception:
        return None
    return bool(achou)


def abrir_spotify():
    log("Spotify fechado. Reabrindo...")
    for exe in (SPOTIFY_EXE, SPOTIFY_EXE_LOJA):
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe])
                return True
            except OSError as e:
                log(f"Falha ao abrir {exe}: {e}")
    try:
        os.startfile("spotify:")  # atalho do Windows, vale para qualquer instalacao
        return True
    except Exception as e:
        log(f"Nao consegui abrir o Spotify ({e}). Ele esta instalado neste usuario?")
        return False


_WINAPI = None


def _winapi():
    # user32/kernel32 com os tipos dos parametros declarados (criado 1 vez so)
    global _WINAPI
    if _WINAPI is None:
        u32 = ctypes.WinDLL("user32")
        k32 = ctypes.WinDLL("kernel32")
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.PostMessageW.restype = wintypes.BOOL
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _WINAPI = (u32, k32)
    return _WINAPI


def _nome_processo(hwnd):
    # Nome do .exe dono da janela ("?" se nao deu pra saber).
    try:
        u32, k32 = _winapi()
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proc:
            return "?"
        try:
            buff = ctypes.create_unicode_buffer(1024)
            tam = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(proc, 0, buff, ctypes.byref(tam)):
                return os.path.basename(buff.value)
        finally:
            k32.CloseHandle(proc)
    except Exception:
        pass
    return "?"


_JANELAS_AVISADAS = set()  # janelas "update" de outros programas ja avisadas no log


def fechar_popup():
    # Fecha o popup de "versao desatualizada" do Spotify no Windows 7
    # usando so ctypes (nativo do Python). Nao precisa de pywin32/pywinauto.
    try:
        user32 = ctypes.windll.user32
        post_message = _winapi()[0].PostMessageW
    except Exception:
        return  # nao e Windows

    WM_CLOSE = 0x0010
    fechadas = []
    ignoradas = []

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            tam = user32.GetWindowTextLengthW(hwnd)
            if tam <= 0:
                return True
            buff = ctypes.create_unicode_buffer(tam + 1)
            user32.GetWindowTextW(hwnd, buff, tam + 1)
            titulo = buff.value or ""
            t = titulo.lower()
            if "desatualiz" in t or "outdated" in t or "update" in t:
                nome = _nome_processo(hwnd)
                if not nome.lower().startswith("spotify"):
                    # Navegador, Windows Update, outros programas: NAO fecha.
                    chave = (titulo, nome)
                    if chave not in _JANELAS_AVISADAS:
                        if len(_JANELAS_AVISADAS) > 500:
                            _JANELAS_AVISADAS.clear()
                        _JANELAS_AVISADAS.add(chave)
                        ignoradas.append(f"'{titulo}' ({nome})")
                    return True
                ok = post_message(hwnd, WM_CLOSE, 0, 0)
                fechadas.append(f"'{titulo}' ({nome})" + ("" if ok else " [falhou]"))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumProc(callback), 0)
        if fechadas:
            log("Popup de versao fechado: " + ", ".join(fechadas))
        if ignoradas:
            log("Janela com 'update' no titulo NAO fechada (nao e do Spotify): "
                + ", ".join(ignoradas))
    except Exception as e:
        log(f"Erro ao tratar popup: {e}")


def esperar_dispositivo(sp, tentativas=12):
    for _ in range(tentativas):
        try:
            devs = sp.devices().get("devices", [])
        except spotipy.SpotifyException as e:
            if (getattr(e, "http_status", None) or 0) < 500:
                raise  # 429 (limite) e 401/403 (login/permissao): o main trata
            devs = []
        if devs:
            return devs
        time.sleep(1.5)
    return []


def nomes_deste_pc():
    nomes = set()
    try:
        nomes.add(socket.gethostname().strip().lower())
    except Exception:
        pass
    nomes.add(os.environ.get("COMPUTERNAME", "").strip().lower())
    nomes.discard("")
    return nomes


def escolher_dispositivo(sp, pb=None):
    # Sempre pega a lista ATUAL de aparelhos: o id que o current_playback()
    # devolve pode ser de um aparelho que ja caiu do Spotify Connect
    # (era isso que dava o 404 "Not found" no /me/player/play).
    devs = [d for d in esperar_dispositivo(sp)
            if d.get("id") and not d.get("is_restricted")]
    if not devs:
        return None

    if DISPOSITIVO:
        alvo = DISPOSITIVO.lower()
        d = next((d for d in devs if (d.get("name") or "").strip().lower() == alvo), None)
        if d is None:
            log(f"Aparelho '{DISPOSITIVO}' do config.json nao esta online. "
                f"Aparelhos online: {listar(devs)}")
        return d

    nomes = nomes_deste_pc()
    for d in devs:
        if (d.get("name") or "").strip().lower() in nomes:
            return d

    computadores = [d for d in devs if tipo(d) == "computer"]
    if len(computadores) == 1:
        return computadores[0]

    # Nao da pra ter certeza de qual e o PC da loja: usa o ultimo aparelho
    # usado (se ainda estiver online), senao o ativo, senao o primeiro.
    # Celular/tablet nunca: a musica tem que sair na loja.
    candidatos = [d for d in devs if tipo(d) not in ("smartphone", "tablet")]
    if not candidatos:
        log(f"Nenhum aparelho da loja online. Aparelhos online: {listar(devs)}")
        return None
    ultimo = ((pb or {}).get("device") or {}).get("id")
    return (next((d for d in candidatos if d["id"] == ultimo), None)
            or next((d for d in candidatos if d.get("is_active")), None)
            or candidatos[0])


def tipo(dev):
    return (dev.get("type") or "").lower()


def descrever(dev):
    return f"'{dev.get('name')}' ({dev.get('type')}, id ...{str(dev.get('id'))[-6:]})"


def listar(devs):
    return ", ".join(descrever(d) for d in devs)


def calcular_offset(sp):
    # Faixa aleatoria: busca o tamanho da playlist so na 1a vez e guarda
    global PLAYLIST_TOTAL
    if not EMBARALHAR:
        return None
    if PLAYLIST_TOTAL is None:
        try:
            info = sp.playlist_items(PLAYLIST_URI, fields="total", limit=1)
            PLAYLIST_TOTAL = int((info or {}).get("total") or 0)
        except spotipy.SpotifyException as e:
            log(f"Nao consegui ler o tamanho da playlist ({resumo_erro(e)}). "
                "Vai comecar do inicio, com o modo aleatorio ligado.")
            if getattr(e, "http_status", None) in (400, 403, 404):
                PLAYLIST_TOTAL = 0  # erro permanente: nem tenta de novo
            return None  # 429/5xx: tenta ler de novo na proxima vez
    if PLAYLIST_TOTAL > 0:
        return {"position": random.randint(0, PLAYLIST_TOTAL - 1)}
    return None


def iniciar_playlist(sp, dev, offset):
    sp.start_playback(device_id=dev["id"], context_uri=PLAYLIST_URI, offset=offset)
    if EMBARALHAR:
        try:
            sp.shuffle(True, device_id=dev["id"])
        except spotipy.SpotifyException as e:
            log(f"Aviso: nao liguei o modo aleatorio ({resumo_erro(e)}).")
    extra = " (faixa aleatoria)" if offset else ""
    log(f"Playlist do GEF retomada em {descrever(dev)}{extra}.")


def garantir_musica(sp):
    global PLAYLIST_TOTAL

    if not dentro_do_horario():
        return

    if spotify_aberto() is False:
        if abrir_spotify():
            time.sleep(10)
            if FECHAR_POPUP:
                fechar_popup()

    # 1 UNICA chamada normalmente: ja diz se esta tocando e em qual aparelho
    pb = sp.current_playback()
    if pb and pb.get("is_playing"):
        return  # tocando -> nao gasta mais nada

    dev = escolher_dispositivo(sp, pb)
    if not dev:
        log("Spotify deste PC nao aparece na API (fechado, iniciando ou "
            "desconectado do Spotify Connect). Tentando de novo no proximo ciclo.")
        return

    offset = calcular_offset(sp)
    try:
        iniciar_playlist(sp, dev, offset)
        return
    except spotipy.SpotifyException as e:
        status = getattr(e, "http_status", None)
        if status != 404 and not (offset and status in (400, 403)):
            raise
        log(f"Nao iniciou em {descrever(dev)}: {resumo_erro(e)}. Tentando recuperar...")

    # Recuperacao: le de novo os aparelhos, "acorda" o do PC com transfer_playback
    # e tenta 1 vez de novo, sem faixa aleatoria.
    if offset:
        PLAYLIST_TOTAL = None  # a playlist pode ter mudado de tamanho
    dev = escolher_dispositivo(sp)
    if not dev:
        log("Spotify deste PC sumiu da lista de aparelhos. Tentando de novo no proximo ciclo.")
        return
    try:
        sp.transfer_playback(dev["id"], force_play=False)
    except spotipy.SpotifyException as e:
        log(f"transfer_playback falhou ({resumo_erro(e)}).")
    time.sleep(2)
    iniciar_playlist(sp, dev, None)


_MUTEX = None


def ja_esta_rodando():
    # Um guardian so por usuario: evita 2 cuidando da musica ao mesmo tempo
    # (atalho da inicializacao + alguem abrindo na mao, atualizacao, etc.).
    global _MUTEX
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        _MUTEX = k32.CreateMutexW(None, False, MUTEX_NOME)
        # 183 = ja existe; 5 = existe, mas foi criado por um guardian aberto como administrador
        return ctypes.get_last_error() in (183, 5)
    except Exception:
        return False


def versao_tupla(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _ler_estado_atualizacao():
    try:
        with open(ESTADO_ATUALIZACAO, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def _gravar_estado_atualizacao(dados):
    tmp = ESTADO_ATUALIZACAO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f)
    os.replace(tmp, ESTADO_ATUALIZACAO)


def _limpar_setups_antigos(manter=None):
    # apaga instaladores (e downloads pela metade) de atualizacoes anteriores
    pasta = tempfile.gettempdir()
    try:
        nomes = os.listdir(pasta)
    except OSError:
        return
    for nome in nomes:
        caminho = os.path.join(pasta, nome)
        if nome.startswith("SpotifyGuardian-Setup-") and caminho != manter:
            try:
                os.remove(caminho)
            except OSError:
                pass


def verificar_atualizacao():
    # Pergunta ao GitHub qual e a ultima versao. Se for mais nova, baixa o
    # instalador, confere o SHA-256 publicado junto e roda em modo silencioso.
    # O instalador fecha este guardian, atualiza e abre o novo.
    if not (ATUALIZACAO_AUTO and URL_ATUALIZACAO):
        return
    agente = {"User-Agent": f"SpotifyGuardian/{VERSAO}"}
    r = requests.get(URL_ATUALIZACAO, timeout=TIMEOUT,
                     headers=dict(agente, Accept="application/vnd.github+json"))
    r.raise_for_status()
    rel = r.json()
    tag = str(rel.get("tag_name") or "")
    if rel.get("draft") or rel.get("prerelease") or versao_tupla(tag) <= versao_tupla(VERSAO):
        return

    links = {a.get("name"): a.get("browser_download_url") for a in (rel.get("assets") or [])}
    url_exe = links.get(ASSET_SETUP)
    url_sha = links.get(ASSET_SETUP + ".sha256")
    if not url_exe or not url_sha:
        log(f"Versao {tag} publicada sem {ASSET_SETUP} e {ASSET_SETUP}.sha256. Ignorando.")
        return

    r = requests.get(url_sha, timeout=TIMEOUT, headers=agente)
    r.raise_for_status()
    achados = re.findall(r"\b[0-9a-fA-F]{64}\b", r.text)
    if not achados:
        log(f"Arquivo .sha256 da versao {tag} esta invalido. Ignorando.")
        return
    esperado = achados[0].lower()

    # O mesmo instalador roda no maximo 2 vezes, com 1 dia entre elas. Evita
    # reinstalar sem parar se ele falhar ou se a tag for maior que a versao
    # que esta dentro dele.
    estado = _ler_estado_atualizacao()
    tentativas = 0
    if estado.get("sha256") == esperado:
        try:
            tentativas = int(estado.get("tentativas", 0))
            desde = abs(time.time() - float(estado.get("ultima", 0)))
        except (TypeError, ValueError):
            tentativas, desde = 0, INTERVALO_ATUALIZACAO
        if tentativas >= MAX_TENTATIVAS_ATUALIZACAO or desde < INTERVALO_ATUALIZACAO - 3600:
            log(f"O instalador da versao {tag} ja rodou aqui {tentativas}x e o guardian continua "
                f"na {VERSAO}. Nao vou tentar de novo agora (veja atualizacao.log).")
            return

    if _mutex_existe(MUTEX_INSTALADOR):
        log(f"Versao {tag} disponivel, mas ha um instalador do Spotify Guardian aberto. "
            "Tento de novo amanha.")
        return

    destino = os.path.join(tempfile.gettempdir(),
                           "SpotifyGuardian-Setup-" + re.sub(r"[^0-9A-Za-z.]", "", tag) + ".exe")
    parcial = destino + ".part"
    _limpar_setups_antigos()
    log(f"Nova versao {tag} disponivel (esta e a {VERSAO}). Baixando...")
    soma = hashlib.sha256()
    tamanho = 0
    inicio = time.monotonic()
    try:
        with requests.get(url_exe, timeout=TIMEOUT, stream=True,
                          headers=dict(agente, Accept="application/octet-stream")) as r:
            r.raise_for_status()
            with open(parcial, "wb") as f:
                for bloco in r.iter_content(256 * 1024):
                    tamanho += len(bloco)
                    if tamanho > 300 * 1024 * 1024:
                        raise ValueError("instalador maior que 300 MB")
                    if time.monotonic() - inicio > PRAZO_DOWNLOAD:
                        raise ValueError(f"download demorou mais de {PRAZO_DOWNLOAD // 60} min")
                    soma.update(bloco)
                    f.write(bloco)
        if soma.hexdigest() != esperado:
            log(f"SHA-256 do instalador {tag} nao confere. Atualizacao cancelada.")
            return
        os.replace(parcial, destino)
    finally:
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass

    try:
        _gravar_estado_atualizacao({"tag": tag, "sha256": esperado,
                                    "tentativas": tentativas + 1, "ultima": time.time()})
    except OSError as e:
        log(f"Nao consegui gravar {ESTADO_ATUALIZACAO} ({e}). Atualizacao cancelada.")
        return

    log(f"Instalando a versao {tag}. O guardian vai reiniciar sozinho.")
    subprocess.Popen(
        [destino, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
         "/LOG=" + os.path.join(PASTA, "atualizacao.log")],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def guardian_rodando():
    # Existe um guardian aberto neste usuario? (o mutex dele existe)
    return _mutex_existe(MUTEX_NOME)


_PARAR_EM_CURSO = []            # parar.ps1 rodando agora
_FECHANDO = []                  # a janela foi fechada / o guardian ja foi reaberto
_TRAVA_PARAR = threading.Lock()


def parar_outros_guardians():
    # Usa o mesmo script do instalador (parar.ps1), que fica na pasta do guardian.
    # Devolve True se parou (ou se nao havia nada rodando).
    script = os.path.join(PASTA, "parar.ps1")
    if not os.path.exists(script):
        print("parar.ps1 nao encontrado na pasta do guardian.")
        return False
    try:
        with _TRAVA_PARAR:
            if _FECHANDO:
                return False
            proc = subprocess.Popen(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Exceto", str(os.getpid()), "-Pasta", PASTA, "-SemAjudantes"],
                creationflags=CREATE_NO_WINDOW)
            _PARAR_EM_CURSO.append(proc)
    except Exception as e:
        print(f"Nao consegui parar o guardian em execucao: {e}")
        return False
    try:
        codigo = proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("O parar.ps1 demorou demais (WMI do Windows travado?).")
        return False
    finally:
        with _TRAVA_PARAR:
            if proc in _PARAR_EM_CURSO:
                _PARAR_EM_CURSO.remove(proc)
    for _ in range(20):  # o mutex some quando o processo antigo termina
        if not guardian_rodando():
            return True
        time.sleep(0.5)
    print(f"O guardian antigo nao fechou (parar.ps1 devolveu {codigo}).")
    return False


def iniciar_em_segundo_plano():
    # Abre um guardian novo sem janela (pythonw.exe ao lado do python.exe).
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen(
        [pythonw if os.path.exists(pythonw) else sys.executable,
         os.path.abspath(__file__), "--sem-espera"],
        cwd=PASTA, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)


def _esperar_guardian_subir(segundos=60):
    for _ in range(segundos * 2):
        if guardian_rodando():
            return True
        time.sleep(0.5)
    return False


def _reabridor():
    # Reabre o guardian UMA vez. Se o parar.ps1 ainda estiver rodando (janela
    # fechada no meio), mata ele antes: senao ele mataria o guardian novo.
    feito = []
    pronto = threading.Event()

    def reabrir():
        with _TRAVA_PARAR:
            ja_foi = bool(feito)
            feito.append(True)
            _FECHANDO.append(True)
            em_curso = list(_PARAR_EM_CURSO)
        if ja_foi:
            # a outra chamada (janela fechada / fim normal) ainda pode estar abrindo o
            # guardian: espera um pouco para o processo nao morrer antes disso
            pronto.wait(4)
            return
        try:
            for proc in em_curso:
                try:
                    proc.kill()
                    proc.wait(3)
                except Exception:
                    pass
            iniciar_em_segundo_plano()
        finally:
            pronto.set()
    return reabrir


def _ignorar_ctrl_c():
    # Nas janelas "Reiniciar" e "Refazer login" o Ctrl+C e ignorado: se ele chegar
    # bem na hora em que o parar.ps1 esta sendo aberto, o Python aborta antes de
    # guardar o processo, e o parar.ps1 solto mataria tambem o guardian reaberto.
    # Para cancelar, e so fechar a janela no X (tratado em _ao_fechar_janela).
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        pass


_MUTEX_AJUDANTE = None


def _segurar_tarefa_vigia():
    # Enquanto esta janela trabalha (guardian fechado de proposito), a tarefa
    # agendada (--vigia) nao abre outro guardian no meio. Se a janela for fechada
    # ou morta, o mutex some sozinho.
    global _MUTEX_AJUDANTE
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        _MUTEX_AJUDANTE = k32.CreateMutexW(None, False, MUTEX_AJUDANTE)
    except Exception:
        _MUTEX_AJUDANTE = None


def _soltar_tarefa_vigia():
    # Solta ANTES do "Enter pra fechar": uma janela esquecida aberta nao pode
    # desligar a tarefa agendada.
    global _MUTEX_AJUDANTE
    h, _MUTEX_AJUDANTE = _MUTEX_AJUDANTE, None
    if h:
        try:
            k32 = ctypes.WinDLL("kernel32")
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle(h)
        except Exception:
            pass


def reiniciar():
    _ignorar_ctrl_c()
    desligar_quickedit()  # um clique na janela nao pode congelar no meio
    print("Reiniciando o Spotify Guardian...")
    reabrir = _reabridor()
    _ao_fechar_janela(reabrir)  # se fecharem a janela no X, o guardian volta mesmo assim
    _segurar_tarefa_vigia()
    parou = subiu = False
    try:
        try:
            parou = parar_outros_guardians()
        finally:
            reabrir()  # qualquer erro no meio tambem reabre
        print("Aguardando o guardian novo abrir...")
        subiu = parou and _esperar_guardian_subir()
    finally:
        _soltar_tarefa_vigia()
    if subiu:
        print("Pronto. O guardian esta rodando de novo (sem janela).")
        time.sleep(3)
    else:
        print("ATENCAO: nao deu pra confirmar que o guardian reiniciou.")
        print("Veja o log (menu Iniciar > Spotify Guardian > Ver log) ou reinicie o computador.")
        input("\nEnter pra fechar...")


_ROTINA_FECHAR = None


def _ao_fechar_janela(acao):
    # Fechar a janela preta no X (ou Ctrl+Break) mata o processo sem rodar o
    # "finally". O Windows avisa antes (CTRL_CLOSE_EVENT / CTRL_BREAK_EVENT).
    global _ROTINA_FECHAR
    try:
        rotina_tipo = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def rotina(evento):
            if evento in (1, 2):  # CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT
                try:
                    acao()
                except Exception:
                    pass
            return False
        _ROTINA_FECHAR = rotina_tipo(rotina)  # precisa continuar referenciada
        k32 = ctypes.WinDLL("kernel32")
        k32.SetConsoleCtrlHandler.argtypes = [rotina_tipo, wintypes.BOOL]
        k32.SetConsoleCtrlHandler(_ROTINA_FECHAR, True)
    except Exception:
        pass


def refazer_login():
    _ignorar_ctrl_c()
    desligar_quickedit()  # um clique na janela nao pode congelar no meio
    print("=" * 55)
    print(" Refazer login do Spotify Guardian")
    print(" (para cancelar, feche esta janela)")
    print("=" * 55)
    novo = CACHE + ".novo"
    reabrir_guardian = _reabridor()
    _ao_fechar_janela(reabrir_guardian)  # se fecharem a janela no X, o guardian volta
    _segurar_tarefa_vigia()  # a tarefa agendada nao pode reabrir o guardian no meio do login
    try:
        print("Parando o guardian que esta rodando...")
        parar_outros_guardians()  # libera a porta 8888 do login
        if os.path.exists(novo):
            os.remove(novo)
        print()
        print("O navegador vai abrir. Entre com a conta Spotify DESTE posto")
        print("e clique em 'Concordo' / 'Autorizar'.")
        print(f"(se ninguem logar em {PRAZO_LOGIN // 60} min, o login antigo continua valendo)")
        me = conectar(cache_path=novo).me()
        os.replace(novo, CACHE)  # so troca o login antigo quando o novo deu certo
        try:
            print()
            log(f"Login do Spotify refeito: {me.get('display_name')} ({me.get('id')})")
        except Exception:
            pass  # o login ja foi gravado: nada aqui pode virar "ERRO no login"
    except Exception as e:
        print()
        print(f"ERRO no login: {resumo_erro(e)}")
        print("O login antigo foi mantido.")
        try:
            os.remove(novo)
        except OSError:
            pass
    finally:
        print("Iniciando o guardian de novo...")
        try:
            if guardian_rodando():
                # abriu um no meio do login (ainda com o login antigo): troca por um novo
                parar_outros_guardians()
        except Exception:
            pass
        finally:
            try:
                reabrir_guardian()
                # a tarefa agendada so volta a valer com o guardian novo ja no ar: senao
                # um "--vigia" que caisse bem nesse intervalo abriria outro guardian
                _esperar_guardian_subir()
            finally:
                _soltar_tarefa_vigia()
    input("\nEnter pra fechar...")


ATRASOS_VIGIA_ANTIGOS = (0, 180, 900)  # s apos abrir: no login o antigo pode abrir depois do novo


def parar_guardians_antigos():
    # Fecha guardians ANTIGOS (de outra pasta: a versao copiada na mao, com
    # iniciar.bat ou guardian.exe) que ainda abrem sozinhos no login, por exemplo
    # por um atalho na inicializacao de todos os usuarios. Dois guardians juntos
    # brigam pelo controle da musica. Nao precisa de administrador.
    # Devolve quantos fechou (None se nao deu pra saber).
    script = os.path.join(PASTA, "parar.ps1")
    if not os.path.exists(script):
        return None
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", script, "-Exceto", str(os.getpid()), "-Pasta", PASTA, "-SoAntigos"],
        creationflags=CREATE_NO_WINDOW, timeout=150,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    achou = re.search(rb"PARADOS=(\d+)", r.stdout or b"")
    return int(achou.group(1)) if achou else None


def xml_tarefa_vigia():
    # Tarefa do Agendador do Windows, so deste usuario e sem administrador: a cada
    # 5 minutos chama "guardian.py --vigia", que so faz algo se o guardian nao
    # estiver rodando. E o que faz o guardian voltar sozinho se for fechado.
    from xml.sax.saxutils import escape
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    usuario = os.environ.get("USERNAME", "")
    if os.environ.get("USERDOMAIN"):
        usuario = os.environ["USERDOMAIN"] + "\\" + usuario
    script = os.path.join(PASTA, "guardian.py")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Reabre o Spotify Guardian GEF se ele for fechado (confere a cada {INTERVALO_VIGIA_MIN} minutos).</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{INTERVALO_VIGIA_MIN}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(usuario)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(pythonw)}</Command>
      <Arguments>{escape('"' + script + '" --vigia')}</Arguments>
      <WorkingDirectory>{escape(PASTA)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args):
    return subprocess.run([SCHTASKS] + list(args), creationflags=CREATE_NO_WINDOW, timeout=60,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def registrar_tarefa_vigia():
    # Cria (ou conserta) a tarefa agendada. So na instalacao feita pelo instalador
    # (pythonw.exe embutido + parar.ps1). Devolve "criada", "ok", "removida" ou None.
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if (getattr(sys, "frozen", False) or not os.path.exists(pythonw)
            or not os.path.exists(os.path.join(PASTA, "parar.ps1"))):
        return None
    from xml.sax.saxutils import escape
    consulta = _schtasks("/Query", "/TN", NOME_TAREFA, "/XML")
    existe = consulta.returncode == 0
    registrada = (consulta.stdout or b"").decode("oem", errors="replace").lower()
    if not REABRIR_SOZINHO:
        if not existe:
            return None
        r = _schtasks("/Delete", "/TN", NOME_TAREFA, "/F")
        if r.returncode != 0:
            saida = " ".join((r.stdout or b"").decode("oem", errors="replace").split())
            log(f"Nao consegui remover a tarefa agendada ({saida}).")
            return None
        log("Tarefa agendada removida (reabrir_sozinho desligado no config.json).")
        return "removida"
    # alguem pode ter desativado ou mexido na tarefa pelo Agendador: ai refaz.
    # Confere o caminho do guardian.py inteiro: so a pasta casaria tambem com uma
    # pasta vizinha de nome parecido ("SpotifyGuardian" x "SpotifyGuardian Copia").
    script = os.path.join(PASTA, "guardian.py")
    inteira = ("<enabled>false</enabled>" not in registrada and "--vigia" in registrada
               and (not PASTA.isascii() or escape(script).lower() in registrada))
    xml = xml_tarefa_vigia()
    marca = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    try:
        with open(ARQ_TAREFA, encoding="utf-8") as f:
            anterior = f.read().strip()
    except OSError:
        anterior = ""
    if existe and inteira and anterior == marca:
        return "ok"
    arq = os.path.join(tempfile.gettempdir(), "spotify_guardian_tarefa.xml")
    with open(arq, "w", encoding="utf-16") as f:
        f.write(xml)
    try:
        r = _schtasks("/Create", "/TN", NOME_TAREFA, "/XML", arq, "/F")
    finally:
        try:
            os.remove(arq)
        except OSError:
            pass
    if r.returncode != 0:
        saida = " ".join((r.stdout or b"").decode("oem", errors="replace").split())
        log(f"Nao consegui criar a tarefa agendada que reabre o guardian ({saida}). "
            "O guardian continua funcionando normalmente.")
        return None
    try:
        with open(ARQ_TAREFA, "w", encoding="utf-8") as f:
            f.write(marca)
    except OSError:
        pass
    log(f"Tarefa agendada criada: se o guardian for fechado, o Windows reabre em ate "
        f"{INTERVALO_VIGIA_MIN} minutos.")
    return "criada"


def _vigiar_guardians_antigos():
    try:
        registrar_tarefa_vigia()
    except Exception as e:
        log(f"Nao consegui conferir a tarefa agendada: {resumo_erro(e)}")
    espera = threading.Event()  # so para esperar, sem mexer no time.sleep do loop principal
    anterior = 0
    for atraso in ATRASOS_VIGIA_ANTIGOS:
        espera.wait(max(0, atraso - anterior))
        anterior = atraso
        try:
            fechados = parar_guardians_antigos()
        except Exception as e:
            log(f"Nao consegui conferir se ha guardian antigo rodando: {resumo_erro(e)}")
            continue
        if fechados:
            log(f"Fechei {fechados} Spotify Guardian antigo(s) que rodava(m) de outra pasta. "
                "Se isto aparecer a cada login, o antigo ainda esta na inicializacao do Windows: "
                "rode o instalador de novo e aceite a permissao de administrador.")


def iniciar_vigia_de_antigos():
    threading.Thread(target=_vigiar_guardians_antigos, daemon=True).start()


class _WTSINFOW(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("SessionId", wintypes.DWORD),
                ("IncomingBytes", wintypes.DWORD), ("OutgoingBytes", wintypes.DWORD),
                ("IncomingFrames", wintypes.DWORD), ("OutgoingFrames", wintypes.DWORD),
                ("IncomingCompressedBytes", wintypes.DWORD),
                ("OutgoingCompressedBytes", wintypes.DWORD),
                ("WinStationName", ctypes.c_wchar * 32), ("Domain", ctypes.c_wchar * 17),
                ("UserName", ctypes.c_wchar * 21),
                ("ConnectTime", ctypes.c_longlong), ("DisconnectTime", ctypes.c_longlong),
                ("LastInputTime", ctypes.c_longlong), ("LogonTime", ctypes.c_longlong),
                ("CurrentTime", ctypes.c_longlong)]


def _minutos_de_sessao():
    # ha quantos minutos este usuario entrou no Windows; 0 se nao der pra saber
    try:
        wts = ctypes.WinDLL("wtsapi32")
        wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wts.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        wts.WTSFreeMemory.restype = None
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        buf = ctypes.c_void_p()
        tam = wintypes.DWORD(0)
        # este PC (None), sessao atual (0xFFFFFFFF), WTSSessionInfo (24)
        if not wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 24,
                                               ctypes.byref(buf), ctypes.byref(tam)):
            return 0.0
        try:
            if not buf.value or tam.value < ctypes.sizeof(_WTSINFOW):
                return 0.0
            info = _WTSINFOW.from_address(buf.value)
            entrou, agora = info.LogonTime, info.CurrentTime
        finally:
            wts.WTSFreeMemory(buf)
        if entrou <= 0 or agora < entrou:
            return 0.0
        return (agora - entrou) / 600000000.0  # unidades de 100 ns -> minutos
    except Exception:
        return 0.0


def _minutos_ligado():
    # Ha quantos minutos o PC foi ligado E o usuario entrou (o menor dos dois).
    # So o tempo ligado do Windows (GetTickCount64) nao serve: com a "Inicializacao
    # rapida" do Windows 10 ele nao zera ao desligar e ligar, so ao reiniciar.
    # Se nao der pra saber, devolve 0 (= recem-ligado: espera o startup_delay).
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.GetTickCount64.restype = ctypes.c_ulonglong
        return min(k32.GetTickCount64() / 60000.0, _minutos_de_sessao())
    except Exception:
        return 0.0


def main():
    pela_tarefa = "--vigia" in sys.argv  # so chega aqui se o guardian NAO estava rodando
    if pela_tarefa and not REABRIR_SOZINHO:
        # desligaram o reabrir_sozinho no config.json e fecharam o guardian sem usar o
        # "Reiniciar": a tarefa que sobrou nao reabre nada, so se remove
        try:
            registrar_tarefa_vigia()
        except Exception as e:
            log(f"Nao consegui conferir a tarefa agendada: {resumo_erro(e)}")
        return
    # PC recem-ligado: a tarefa pode disparar antes do atalho da inicializacao; ai
    # espera o startup_delay normal. Com o PC ligado ha mais tempo, abre direto.
    sem_espera = "--sem-espera" in sys.argv or (pela_tarefa and _minutos_ligado() > 5)
    if ja_esta_rodando():
        log("Ja existe um Spotify Guardian rodando neste usuario. Saindo.")
        return
    desligar_quickedit()
    log("=" * 50)
    log(f"Spotify Guardian GEF {VERSAO} iniciado"
        + (" (aberto pela tarefa agendada: nao havia guardian rodando)." if pela_tarefa else "."))
    log(f"Python {sys.version.split()[0]}{' (exe)' if getattr(sys, 'frozen', False) else ''} | "
        f"spotipy {versao('spotipy')} | requests {versao('requests')} | urllib3 {versao('urllib3')}")
    if PLAYLIST_URI != PLAYLIST_CFG:
        log(f"playlist_uri do config.json ajustada para {PLAYLIST_URI}")
    if not re.match(r"^spotify:playlist:[0-9A-Za-z]{22}$", PLAYLIST_URI):
        log(f"ATENCAO: playlist_uri parece invalida: {PLAYLIST_URI!r}")
    if not URL_ATUALIZACAO:
        log("Atualizacao automatica desligada (sem repositorio configurado).")
    elif not ATUALIZACAO_AUTO:
        log("Atualizacao automatica desligada no config.json.")
    iniciar_vigia_de_antigos()
    if STARTUP_DELAY and not sem_espera:
        log(f"Aguardando {STARTUP_DELAY:g}s de inicializacao...")
        time.sleep(STARTUP_DELAY)

    sp = conectar()
    falhas_rede = 0
    proxima_atualizacao = time.monotonic() + ATUALIZACAO_ATRASO

    while True:
        espera = INTERVALO
        if time.monotonic() >= proxima_atualizacao:
            proxima_atualizacao = time.monotonic() + INTERVALO_ATUALIZACAO
            try:
                verificar_atualizacao()
            except Exception as e:
                log(f"Nao consegui verificar atualizacao: {resumo_erro(e)}")
        try:
            if FECHAR_POPUP:
                fechar_popup()
            garantir_musica(sp)
            falhas_rede = 0
        except requests.exceptions.RequestException as e:
            # Queda de internet / conexao derrubada: tenta de novo logo.
            falhas_rede += 1
            if falhas_rede <= MAX_FALHAS_RAPIDAS:
                espera = ESPERA_FALHA_REDE
            log(f"Falha de rede ({falhas_rede}a seguida): {resumo_erro(e)}. "
                f"Nova tentativa em {espera}s.")
        except spotipy.SpotifyException as e:
            status = getattr(e, "http_status", None)
            if status == 429:
                # Limite atingido: respeita o tempo que o Spotify pedir.
                retry = 0
                try:
                    retry = int((e.headers or {}).get("Retry-After", 0))
                except Exception:
                    retry = 0
                espera = min(max(retry, 60), 3600) if retry else 900
                log(f"Limite de requisicoes atingido. Aguardando {espera}s antes de tentar de novo.")
            elif status and status >= 500:
                espera = 60
                log(f"Spotify instavel ({resumo_erro(e)}). Nova tentativa em {espera}s.")
            else:
                log(f"Erro Spotify: {resumo_erro(e)}")
        except SpotifyOauthError as e:
            if getattr(e, "error", None) == "invalid_grant" or "invalid_grant" in str(e):
                espera = 1800
                log("LOGIN DO SPOTIFY EXPIRADO (o Spotify exige novo login a cada 6 meses). "
                    "Use menu Iniciar > Spotify Guardian > 'Refazer login do Spotify'.")
            elif login_pendente(e):
                espera = 1800
                log("Login do Spotify pendente: ninguem entrou com a conta no navegador "
                    "(ou o login foi cancelado). "
                    "Use menu Iniciar > Spotify Guardian > 'Refazer login do Spotify'.")
            else:
                log(f"Erro no login do Spotify: {e}")
        except Exception as e:
            log(f"Erro no loop: {resumo_erro(e)}")
        time.sleep(espera)


if __name__ == "__main__":
    if "--relogin" in sys.argv:
        refazer_login()
    elif "--reiniciar" in sys.argv:
        reiniciar()
    else:
        main()
