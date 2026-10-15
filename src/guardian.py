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

  - Modo painel (2.1.0): pareado com o painel Musica GEF, a playlist, o horario e o
    login do Spotify vem do painel; o PC manda o que esta tocando e recebe comandos.
    Sem pareamento, o config.json com client_id continua funcionando como antes.

Requisitos: conta Spotify PREMIUM + app no Spotify for Developers.
Instalado pelo SpotifyGuardian-Setup.exe, que ja traz o Python junto
(3.8 no Windows 7, 3.14 no Windows 10/11).

Uso:
  guardian.py                 roda o guardian (e o que a inicializacao do Windows chama)
  guardian.py --sem-espera    idem, sem o startup_delay
  guardian.py --vigia         chamado pela tarefa agendada a cada 5 min: so abre o
                              guardian se ele nao estiver rodando
  guardian.py --relogin       refaz o login do Spotify (apaga o .cache_gef). No modo
                              painel so orienta: o login e feito no painel
  guardian.py --reiniciar     reinicia o guardian (ex.: depois de editar o config.json)
  guardian.py --parear CODIGO [--url URL]
                              pareia este PC com o painel (usado pelo instalador). Sai com
                              0 ok, 2 formato, 3 codigo invalido, 4 rede, 5 resposta,
                              6 gravacao, 7 URL; imprime RESULTADO=<n> e PCNOME=<nome>
  guardian.py --conectar-painel   pede o codigo de 6 digitos e pareia (menu Iniciar)
  guardian.py --desparear     desconecta este PC do painel (so para suporte)
"""

import os
import re
import sys
import time
import json
import base64
import ctypes
import socket
import random
import signal
import hashlib
import logging
import tempfile
import threading
import subprocess
import collections
import unicodedata
from ctypes import wintypes
from datetime import datetime
from urllib.parse import urlsplit

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

VERSAO = "2.1.0"
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


_TRAVA_LOG = threading.Lock()                  # no modo painel duas threads escrevem no log
ERROS_RECENTES = collections.deque(maxlen=20)  # (monotonic, hora, mensagem): vao no heartbeat


def log(msg, erro=False):
    agora = datetime.now()
    linha = f"[{agora:%d/%m/%Y %H:%M:%S}] {msg}"
    with _TRAVA_LOG:
        if erro:
            ERROS_RECENTES.append((time.monotonic(), f"{agora:%d/%m/%Y %H:%M:%S}", str(msg)[:300]))
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


_LOG_LIMITADO = {}


def log_limitado(chave, msg, a_cada=3600):
    # A mesma situacao repetindo (painel fora do ar, conta pendente): anota no maximo
    # 1 vez por hora, para o guardian.log nao virar so isso.
    agora = time.monotonic()
    ultimo = _LOG_LIMITADO.get(chave)
    if ultimo is None or agora - ultimo >= a_cada:
        _LOG_LIMITADO[chave] = agora
        log(msg, erro=True)


def mascarar(texto):
    # Tira o que parece token/segredo antes de o texto sair do PC (log enviado ao painel).
    texto = re.sub(r"(?i)\b(bearer\s+)\S+", r"\1***", texto)
    texto = re.sub(r"(?i)\b(access_token|refresh_token|client_secret|token|code)"
                   r"([\"']?\s*[=:]\s*[\"']?)[^\s\"'&,}]+", r"\1\2***", texto)
    texto = re.sub(r"[A-Za-z0-9_\-]{40,}", "***", texto)
    return re.sub(r"\b[0-9a-fA-F]{32}\b", "***", texto)


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


# ---------- identidade no painel (painel.json) ----------
URL_PAINEL = "https://musica.postogef.com.br"
ARQ_PAINEL = os.path.join(PASTA, "painel.json")  # quem e este PC no painel; o token vai protegido
ENTROPIA_DPAPI = b"SpotifyGuardianGEF/painel/1"


def validar_url_painel(url):
    # So https. http so em 127.0.0.1/localhost (testes). Devolve "esquema://host[:porta]".
    partes = urlsplit(str(url or "").strip())
    host = (partes.hostname or "").lower()
    partes.port  # porta invalida da ValueError aqui
    if (not host or partes.username or partes.password or partes.query or partes.fragment
            or partes.path not in ("", "/")):
        raise ValueError("URL do painel invalida")
    if partes.scheme != "https" and not (partes.scheme == "http" and host in ("127.0.0.1", "localhost")):
        raise ValueError("URL do painel precisa ser https")
    return f"{partes.scheme}://{partes.netloc}"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(dados, proteger):
    # Protege (ou abre) dados com a DPAPI do Windows: so este usuario, neste PC, abre de volta.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32")
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    funcao = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    funcao.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    funcao.restype = wintypes.BOOL
    buf_dados = ctypes.create_string_buffer(dados, len(dados))
    buf_entropia = ctypes.create_string_buffer(ENTROPIA_DPAPI, len(ENTROPIA_DPAPI))
    entrada = _BLOB(len(dados), ctypes.cast(buf_dados, ctypes.POINTER(ctypes.c_char)))
    entropia = _BLOB(len(ENTROPIA_DPAPI), ctypes.cast(buf_entropia, ctypes.POINTER(ctypes.c_char)))
    saida = _BLOB()
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: nunca abre janela
    if not funcao(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, 0x1,
                  ctypes.byref(saida)):
        raise OSError(ctypes.get_last_error(), "a DPAPI do Windows recusou os dados")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        k32.LocalFree(saida.pbData)


def _gravar_json_atomico(caminho, dados):
    # grava num .tmp e so entao troca: queda de energia no meio nunca deixa o arquivo pela metade
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, caminho)


def gravar_identidade(url, pc_id, pc_nome, token):
    protegido = base64.b64encode(_dpapi(token.encode("ascii"), True)).decode("ascii")
    _gravar_json_atomico(ARQ_PAINEL, {"url": url, "pcId": pc_id, "pcNome": pc_nome,
                                      "token": protegido})


def ler_identidade():
    # {url, pcId, pcNome, token} do painel.json. None se nao existe ou nao deu pra ler
    # (outro usuario do Windows, arquivo copiado de outro PC, arquivo estragado...).
    try:
        with open(ARQ_PAINEL, encoding="utf-8") as f:
            dados = json.load(f)
        token = _dpapi(base64.b64decode(dados["token"]), False).decode("ascii")
        pc_id = dados["pcId"]
        if not token or not isinstance(pc_id, str) or not pc_id:
            return None
        return {"url": validar_url_painel(dados.get("url") or URL_PAINEL), "pcId": pc_id,
                "pcNome": str(dados.get("pcNome") or ""), "token": token}
    except Exception:
        return None


def esquecer_identidade():
    # painel.json -> painel.json.revogado (fica para o suporte ver; um pareamento novo grava outro)
    try:
        os.replace(ARQ_PAINEL, ARQ_PAINEL + ".revogado")
        return True
    except OSError as e:
        log(f"Nao consegui desconectar este PC do painel ({type(e).__name__}: {e}).", erro=True)
        return False


def carregar_config_local():
    # config.json do PC: {} se nao existe; None se existe mas nao deu pra ler
    caminho = os.path.join(PASTA, "config.json")
    if not os.path.exists(caminho):
        return {}
    try:
        cfg = ler_config(caminho)
    except Exception:
        return None
    return cfg if isinstance(cfg, dict) else None


def decidir_modo(cfg_local, identidade):
    # painel   : pareado (painel.json legivel). Playlist, horario e login vem do painel.
    # legado   : config.json com client_id e nunca pareado. Tudo como antes da 2.1.0.
    # sem_conta: nenhum dos dois. Segura o lugar e espera um pareamento.
    if identidade:
        return "painel"
    if os.path.exists(ARQ_PAINEL) or os.path.exists(ARQ_PAINEL + ".revogado"):
        return "sem_conta"  # ja foi pareado: nunca volta sozinho para o login antigo
    if cfg_local is None or "client_id" in cfg_local:
        return "legado"  # config.json estragado tambem: o bloco antigo mostra o erro como sempre
    return "sem_conta"


def _numero_local(cfg, chave, padrao):
    # numero do config.json (tambem escrito como texto); faltando ou lixo vira o padrao
    try:
        valor = float(cfg[chave])
    except (KeyError, TypeError, ValueError):
        return padrao
    return min(max(valor, 0.0), 86400.0) if valor == valor else padrao


# ---------- carrega config.json ----------
CFG_LOCAL = carregar_config_local()
IDENTIDADE = ler_identidade()
MODO = decidir_modo(CFG_LOCAL, IDENTIDADE)
# o pareamento nao depende do config.json do modo antigo (nem de ele estar certo)
_SO_PAREAMENTO = __name__ == "__main__" and any(
    a in sys.argv for a in ("--parear", "--conectar-painel", "--desparear"))
if MODO == "legado" and not _SO_PAREAMENTO:
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
else:
    # Modo painel ou sem conta: o config.json e opcional e so vale para as chaves que
    # ficam no PC (startup_delay, fechar_popup, url_atualizacao, atualizacao_atraso_s).
    # O resto vem do painel (aplicar_config_painel). Nada aqui pode quebrar.
    CFG = CFG_LOCAL or {}
    CLIENT_ID = CLIENT_SECRET = REDIRECT_URI = None
    PLAYLIST_CFG = PLAYLIST_URI = None   # None = sem playlist: so mantem o Spotify aberto
    EMBARALHAR = True
    INTERVALO = 180
    STARTUP_DELAY = _numero_local(CFG, "startup_delay", 0.0)
    HORARIO_INICIO = HORARIO_FIM = None
    DISPOSITIVO = ""
    FECHAR_POPUP = CFG.get("fechar_popup", eh_windows7())
    ATUALIZACAO_AUTO = True
    URL_ATUALIZACAO = ((CFG.get("url_atualizacao") if isinstance(CFG.get("url_atualizacao"), str) else "")
                       or (f"https://api.github.com/repos/{REPO_ATUALIZACAO}/releases/latest"
                           if REPO_ATUALIZACAO else ""))
    ATUALIZACAO_ATRASO = int(_numero_local(CFG, "atualizacao_atraso_s", 600))
    REABRIR_SOZINHO = True

PLAYLIST_TOTAL = None  # tamanho da playlist (buscado 1x e guardado)
# modo painel (aplicar_config_painel e heartbeat); no modo legado ficam assim
PLAYLIST_NOME = ""
PAUSADO_ATE = None             # time.monotonic() ate quando o painel pausou a musica
CONFIG_ERROS = []              # chaves da config do painel que vieram com valor invalido
VERSAO_CONFIG_APLICADA = None
PAINEL = None                  # Painel (so no modo painel)
RETRATO = None                 # (time.monotonic(), reproducao) do ultimo current_playback
ULTIMO_DISPOSITIVO = None      # aparelho deste PC no Spotify Connect
_ULTIMO_CICLO = None           # time.monotonic() do ultimo ciclo sem erro
_ACORDAR = threading.Event()   # o painel mandou comando/config nova: o laco principal acorda

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

# ---------- painel Musica GEF (contrato: protocolo 1) ----------
ARQ_ESTADO_PAINEL = os.path.join(PASTA, "painel_estado.json")  # config em cache, comandos feitos
PLAYLIST_VALIDA = r"^spotify:playlist:[0-9A-Za-z]{22}$"
INTERVALO_HEARTBEAT = 300      # o painel pode mudar (intervaloHeartbeatSeg)
CONSULTA_NOVIDADES = 60        # idem (proximaConsultaSeg, de 15 a 900)
ESPERA_PAINEL_MAX = 900        # painel fora do ar: tenta de novo em 60, 120, 240... ate 15 min
RENOVAR_TOKEN_ANTES = 600      # pede outro access token 10 min antes de o atual vencer
ESPERA_CONTA_PENDENTE = 600    # conta Spotify do PC precisa de atencao no painel: confere a cada 10 min
ESPERA_SEM_CONTA = 30
PRAZO_COMANDO = 30 * 60        # comando com mais de 30 min nao roda mais
REVOGACAO_RESPOSTAS = 3        # "token_revogado" so vale depois de 3 respostas...
REVOGACAO_PRAZO = 600          # ...em pelo menos 10 min
TAMANHO_MAX_LOG = 64 * 1024    # comando enviar_log
COMANDOS_DO_LACO = ("reabrir_spotify", "reiniciar_guardian", "verificar_atualizacao", "retomar_musica")
COMANDOS_DO_PAINEL = ("sincronizar", "enviar_log")   # feitos pela propria thread do painel
# preferencias do app do Spotify: dizem qual conta esta logada neste PC
ARQS_PREFS_SPOTIFY = (os.path.expandvars(r"%APPDATA%\Spotify\prefs"),
                      os.path.expandvars(r"%LOCALAPPDATA%\Packages\SpotifyAB.SpotifyMusic_zpdnekdrzrea0"
                                         r"\LocalState\Spotify\prefs"))
# codigos de saida do --parear (o instalador le)
SAIDA_OK, SAIDA_FORMATO, SAIDA_CODIGO, SAIDA_REDE, SAIDA_RESPOSTA, SAIDA_GRAVACAO, SAIDA_URL = (
    0, 2, 3, 4, 5, 6, 7)


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
    if MODO == "painel":
        # o access token vem do painel (TokenDoPainel): o navegador nunca abre neste modo
        return spotipy.Spotify(
            auth_manager=TokenDoPainel(PAINEL or Painel(IDENTIDADE)),
            requests_timeout=TIMEOUT,
            requests_session=False,
        )
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
            log(f"Aparelho '{DISPOSITIVO}' do {'painel' if MODO == 'painel' else 'config.json'} "
                f"nao esta online. Aparelhos online: {listar(devs)}")
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
    registrar_dispositivo(dev)
    if EMBARALHAR:
        try:
            sp.shuffle(True, device_id=dev["id"])
        except spotipy.SpotifyException as e:
            log(f"Aviso: nao liguei o modo aleatorio ({resumo_erro(e)}).")
    extra = " (faixa aleatoria)" if offset else ""
    log(f"Playlist do GEF retomada em {descrever(dev)}{extra}.")


def pausado():
    # o painel pediu para nao retomar a musica ate um horario (pausadoAte, Fase 2)
    return PAUSADO_ATE is not None and time.monotonic() < PAUSADO_ATE


def registrar_dispositivo(dev):
    global ULTIMO_DISPOSITIVO
    ULTIMO_DISPOSITIVO = {"id": dev.get("id"), "nome": dev.get("name"), "tipo": dev.get("type"),
                          "volume": dev.get("volume_percent"), "ativo": bool(dev.get("is_active"))}


def registrar_reproducao(pb):
    # Guarda o que o current_playback do garantir_musica ja trouxe (sem chamada extra ao
    # Spotify) para o heartbeat do painel. Nunca levanta erro.
    global RETRATO
    try:
        if not pb:
            RETRATO = (time.monotonic(), None)
            return
        item = pb.get("item") or {}
        album = item.get("album") or {}
        capas = album.get("images") or []
        RETRATO = (time.monotonic(), {
            "tocando": bool(pb.get("is_playing")),
            "faixa": item.get("name"),
            "artistas": [a.get("name") for a in item.get("artists") or [] if a and a.get("name")],
            "album": album.get("name"),
            "capaUrl": (capas[0] or {}).get("url") if capas else None,
            "faixaUri": item.get("uri"),
            "contextoUri": (pb.get("context") or {}).get("uri"),
            "duracaoMs": item.get("duration_ms"),
            "progressoMs": pb.get("progress_ms"),
        })
        dev = pb.get("device")
        if dev and dev.get("id") and tipo(dev) not in ("smartphone", "tablet"):
            registrar_dispositivo(dev)
    except Exception:
        pass


def garantir_musica(sp):
    global PLAYLIST_TOTAL

    if not dentro_do_horario():
        return

    if spotify_aberto() is False:
        if abrir_spotify():
            time.sleep(10)
            if FECHAR_POPUP:
                fechar_popup()

    if PLAYLIST_URI is None or pausado():
        return  # modo painel sem playlist (ou pausado pelo painel): so mantem o Spotify aberto

    # 1 UNICA chamada normalmente: ja diz se esta tocando e em qual aparelho
    pb = sp.current_playback()
    registrar_reproducao(pb)
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


def verificar_atualizacao(forcar=False):
    # Pergunta ao GitHub qual e a ultima versao. Se for mais nova, baixa o
    # instalador, confere o SHA-256 publicado junto e roda em modo silencioso.
    # O instalador fecha este guardian, atualiza e abre o novo.
    # forcar: comando "verificar_atualizacao" do painel; vale mesmo com a atualizacao
    # automatica desligada. As outras regras (SHA-256, no maximo 2x) sao as mesmas.
    # Devolve um texto curto com o resultado (vai para o painel).
    if not URL_ATUALIZACAO:
        return "sem repositorio de atualizacao configurado"
    if not (ATUALIZACAO_AUTO or forcar):
        return "atualizacao automatica desligada"
    agente = {"User-Agent": f"SpotifyGuardian/{VERSAO}"}
    r = requests.get(URL_ATUALIZACAO, timeout=TIMEOUT,
                     headers=dict(agente, Accept="application/vnd.github+json"))
    r.raise_for_status()
    rel = r.json()
    tag = str(rel.get("tag_name") or "")
    if rel.get("draft") or rel.get("prerelease") or versao_tupla(tag) <= versao_tupla(VERSAO):
        return f"ja esta na versao mais nova ({VERSAO})"

    links = {a.get("name"): a.get("browser_download_url") for a in (rel.get("assets") or [])}
    url_exe = links.get(ASSET_SETUP)
    url_sha = links.get(ASSET_SETUP + ".sha256")
    if not url_exe or not url_sha:
        log(f"Versao {tag} publicada sem {ASSET_SETUP} e {ASSET_SETUP}.sha256. Ignorando.")
        return f"versao {tag} publicada sem o instalador e o .sha256"

    r = requests.get(url_sha, timeout=TIMEOUT, headers=agente)
    r.raise_for_status()
    achados = re.findall(r"\b[0-9a-fA-F]{64}\b", r.text)
    if not achados:
        log(f"Arquivo .sha256 da versao {tag} esta invalido. Ignorando.")
        return f"arquivo .sha256 da versao {tag} invalido"
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
            return f"o instalador da versao {tag} ja rodou aqui {tentativas}x (veja atualizacao.log)"

    if _mutex_existe(MUTEX_INSTALADOR):
        log(f"Versao {tag} disponivel, mas ha um instalador do Spotify Guardian aberto. "
            "Tento de novo amanha.")
        return f"versao {tag} disponivel, mas ha um instalador aberto"

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
            return f"SHA-256 do instalador {tag} nao confere"
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
        return f"nao consegui gravar {os.path.basename(ESTADO_ATUALIZACAO)}"

    log(f"Instalando a versao {tag}. O guardian vai reiniciar sozinho.")
    subprocess.Popen(
        [destino, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
         "/LOG=" + os.path.join(PASTA, "atualizacao.log")],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return f"instalando a versao {tag}"


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


# ====================================================================
# Modo painel: o PC conversa com o painel Musica GEF (rotas /api/guardian/*).
# Contrato: docs/CONTRATO-GUARDIAN.md no repositorio do painel (protocolo 1).
# ====================================================================

class PainelIndisponivel(requests.exceptions.RequestException):
    # Painel fora do ar, sem internet ou com erro. E um RequestException: no laco
    # principal cai no mesmo tratamento de "Falha de rede".
    def __init__(self, mensagem, status=None, codigo=None, tentar_em=None):
        super().__init__(mensagem)
        self.status, self.codigo, self.tentar_em = status, codigo, tentar_em


class RespostaInvalida(PainelIndisponivel):
    # o painel respondeu fora do contrato (redirecionamento, sem JSON, faltando campo)
    pass


class ContaPendente(Exception):
    # 409 do /token: a conta Spotify deste PC precisa de atencao no painel
    def __init__(self, motivo):
        super().__init__(f"a conta Spotify deste PC precisa de atencao no painel ({motivo})")
        self.motivo = motivo


class TokenRevogado(Exception):
    # 401 {"motivo": "token_revogado"}: o painel nao reconhece mais este PC
    def __init__(self):
        super().__init__("o painel nao reconhece mais o token deste PC")


class NaoSuportado(Exception):
    # 404 no /novidades: painel sem essa rota; o guardian fica so com o heartbeat
    pass


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _faixa(valor, minimo, maximo, padrao):
    # numero do painel dentro de [minimo, maximo]; se nao for numero, o padrao
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != valor:
        return padrao
    return int(min(max(valor, minimo), maximo))


def versao_windows():
    try:
        v = sys.getwindowsversion()
        return f"{v.major}.{v.minor}.{v.build}"
    except Exception:
        return ""


def usuario_spotify_local():
    # Conta logada no app do Spotify deste PC (do arquivo prefs dele); None se nao achar.
    # O painel avisa quando ela e diferente da conta do painel.
    for prefs in ARQS_PREFS_SPOTIFY:
        try:
            with open(prefs, encoding="utf-8", errors="replace") as f:
                texto = f.read()
        except OSError:
            continue
        for chave in ("autologin.canonical_username", "autologin.username"):
            achado = re.search(r'^' + re.escape(chave) + r'="([^"]*)"', texto, re.M)
            if achado and achado.group(1):
                return achado.group(1)
    return None


def ler_fim_do_log(linhas):
    # as ultimas `linhas` linhas do guardian.log (o arquivo tem no maximo ~2 MB)
    try:
        with open(LOG_FILE, "rb") as f:
            texto = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(texto.splitlines()[-linhas:])


_REVOGACOES = []                       # time.monotonic() de cada "token_revogado" seguido
_TRAVA_REVOGACAO = threading.Lock()


def tratar_revogacao():
    # O painel disse que o token deste PC nao vale mais. So acredita depois de 3 respostas
    # em pelo menos 10 min (um painel com defeito nao pode desconectar as lojas de uma vez).
    # Confirmado: painel.json vira .revogado e o guardian reinicia no modo sem_conta
    # (se o painel.json do disco ja for de um pareamento novo, so reinicia).
    agora = time.monotonic()
    with _TRAVA_REVOGACAO:
        _REVOGACOES.append(agora)
        confirmada = (len(_REVOGACOES) >= REVOGACAO_RESPOSTAS
                      and agora - _REVOGACOES[0] >= REVOGACAO_PRAZO)
    if not confirmada:
        log_limitado("revogado", "O painel recusou o token deste PC. Se continuar assim por "
                     f"{REVOGACAO_PRAZO // 60} min, este PC sera desconectado do painel.")
        return
    em_disco = ler_identidade()
    if em_disco and IDENTIDADE and em_disco["token"] != IDENTIDADE["token"]:
        # o PC foi pareado de novo enquanto este processo rodava: o painel.json do disco
        # e o novo e nao pode virar .revogado. Reinicia para usar o token novo.
        reiniciar_processo("pareamento novo no painel.json")
        return
    log("Este PC foi desconectado no painel (token revogado). Esperando um pareamento novo: "
        "menu Iniciar > Spotify Guardian > 'Conectar ao painel'.", erro=True)
    if esquecer_identidade():
        reiniciar_processo("PC desconectado do painel")


def _zerar_revogacoes():
    with _TRAVA_REVOGACAO:
        del _REVOGACOES[:]


class Painel:
    # Conversa com o painel e guarda o estado em painel_estado.json: config em cache,
    # versao, ids dos comandos ja recebidos (para nunca repetir um reiniciar_guardian),
    # comandos ainda nao feitos e resultados ainda nao entregues.

    def __init__(self, identidade):
        self.url = identidade["url"]
        self.token_pc = identidade.get("token")   # token deste PC no painel
        self.pc_nome = identidade.get("pcNome") or ""
        self.intervalo_hb = INTERVALO_HEARTBEAT
        self.consulta = CONSULTA_NOVIDADES
        self.com_novidades = True
        self.proximo_hb = self.proxima_nov = 0.0   # time.monotonic(); 0 = na hora
        self.falhas = 0
        self.ja_respondeu = False
        self.conta_em_uso = None     # contaSpotifyId do ultimo /token
        self.conta_trocou = False    # o heartbeat trouxe outra conta: o token em cache nao vale mais
        self.conta_pendente = None   # motivo do ultimo 409 do /token (None = conta ok)
        self.config_nova = None      # (config, versao, servidorMs, recebido_em) para o laco principal
        self._trava = threading.RLock()
        self._pedido = threading.Event()   # heartbeat pedido agora (sincronizar, resultado novo)
        self.estado = self._ler_estado()

    # ---------- estado em disco ----------
    def _ler_estado(self):
        try:
            with open(ARQ_ESTADO_PAINEL, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            dados = {}
        if not isinstance(dados, dict):
            dados = {}
        for chave in ("comandosFeitos", "resultadosPendentes", "comandosPendentes"):
            if not isinstance(dados.get(chave), list):
                dados[chave] = []
        if not isinstance(dados.get("config"), dict):
            dados["config"] = None
        agora = time.monotonic()
        dados["comandosPendentes"] = [dict(c, recebido=agora) for c in dados["comandosPendentes"]
                                      if isinstance(c, dict) and c.get("id") and c.get("tipo")]
        return dados

    def _gravar_estado(self):
        with self._trava:
            try:
                _gravar_json_atomico(ARQ_ESTADO_PAINEL, self.estado)
            except (OSError, ValueError, TypeError) as e:
                log_limitado("estado_painel", f"Nao consegui gravar {ARQ_ESTADO_PAINEL} ({resumo_erro(e)}).")

    def config_em_cache(self):
        with self._trava:
            return self.estado.get("config"), self.estado.get("versaoConfig")

    # ---------- HTTP ----------
    def _pedir(self, metodo, rota, corpo=None):
        cabecalhos = {"User-Agent": f"SpotifyGuardian/{VERSAO}", "Accept": "application/json"}
        if self.token_pc:
            cabecalhos["Authorization"] = "Bearer " + self.token_pc
        try:
            # timeout sempre explicito e sem seguir redirecionamento: o painel nunca
            # redireciona; um 3xx e portal de wi-fi, proxy ou erro de configuracao
            r = requests.request(metodo, self.url + rota, json=corpo, headers=cabecalhos,
                                 timeout=TIMEOUT, allow_redirects=False)
        except requests.exceptions.RequestException as e:
            raise PainelIndisponivel(f"{rota}: {resumo_erro(e)}")
        try:
            dados = r.json()
        except ValueError:
            dados = None
        if 200 <= r.status_code < 300:
            if not isinstance(dados, dict):
                raise RespostaInvalida(f"{rota}: HTTP {r.status_code} sem JSON", status=r.status_code)
            if self.token_pc:
                _zerar_revogacoes()  # o token vale
            return dados
        dados = dados if isinstance(dados, dict) else {}
        motivo, codigo = dados.get("motivo"), dados.get("codigo")
        if r.status_code == 401 and self.token_pc and motivo == "token_revogado":
            raise TokenRevogado()  # qualquer outro 401 (pagina de protecao, sem cabecalho...) nao e revogacao
        if r.status_code == 409 and motivo in ("sem_conta", "reautorizar", "conta_com_erro"):
            raise ContaPendente(motivo)
        classe = RespostaInvalida if 300 <= r.status_code < 400 else PainelIndisponivel
        raise classe(f"{rota}: HTTP {r.status_code}" + (f" {codigo}" if codigo else ""),
                     status=r.status_code, codigo=codigo, tentar_em=dados.get("tentarEmSeg"))

    def token(self, forcar):
        try:
            r = self._pedir("POST", "/api/guardian/token", {"forcar": bool(forcar)})
        except ContaPendente as e:
            self.conta_pendente = e.motivo
            raise
        if self.conta_pendente:
            log("A conta Spotify deste PC voltou a funcionar no painel.")
            _LOG_LIMITADO.pop("conta_pendente", None)
        self.conta_pendente = None
        self.conta_em_uso = r.get("contaSpotifyId")
        return r

    def novidades(self):
        # True se o carimbo mudou (ha novidade: manda um heartbeat ja)
        try:
            r = self._pedir("GET", "/api/guardian/novidades")
        except PainelIndisponivel as e:
            if e.status == 404:
                raise NaoSuportado()
            raise
        self.consulta = _faixa(r.get("proximaConsultaSeg"), 15, 900, self.consulta)
        carimbo = r.get("carimbo")
        with self._trava:
            return isinstance(carimbo, str) and carimbo != self.estado.get("carimbo")

    def heartbeat(self):
        corpo = self.montar_heartbeat()
        r = self._pedir("POST", "/api/guardian/heartbeat", corpo)
        with self._trava:
            entregues = corpo["resultados"]
            self.estado["resultadosPendentes"] = [x for x in self.estado["resultadosPendentes"]
                                                  if x not in entregues]
        if not self.ja_respondeu:
            self.ja_respondeu = True
            log(f"Painel ok: PC '{self.pc_nome}', configuracao versao {r.get('versaoConfig')}.")
        self.receber(r)
        return r

    def enviar_log(self, comando_id, linhas):
        texto = mascarar(ler_fim_do_log(linhas))
        bruto = texto.encode("utf-8")
        if len(bruto) > TAMANHO_MAX_LOG:
            texto = bruto[-TAMANHO_MAX_LOG:].decode("utf-8", errors="ignore")  # fica o fim
        self._pedir("POST", "/api/guardian/log", {"comandoId": comando_id, "texto": texto})

    def montar_heartbeat(self):
        agora = time.monotonic()
        with self._trava:
            resultados = list(self.estado["resultadosPendentes"])
        with _TRAVA_LOG:
            erros = list(ERROS_RECENTES)
        retrato = RETRATO
        reproducao = None
        if retrato and retrato[1]:
            reproducao = dict(retrato[1], idadeSeg=int(agora - retrato[0]))
        try:
            aberto = spotify_aberto()
        except Exception:
            aberto = None
        return {
            "protocolo": 1, "versaoGuardian": VERSAO, "modo": "painel",
            "versaoConfigAplicada": VERSAO_CONFIG_APLICADA,
            "contaSpotifyIdEmUso": self.conta_em_uso,
            "sistema": {"computador": os.environ.get("COMPUTERNAME") or socket.gethostname(),
                        "usuario": os.environ.get("USERNAME", ""), "windows": versao_windows(),
                        "python": sys.version.split()[0], "ligadoHaMin": int(_minutos_ligado()),
                        "relogioPcMs": int(time.time() * 1000)},
            "spotify": {"aberto": aberto, "usuarioLogado": usuario_spotify_local()},
            "reproducao": reproducao,
            "dispositivo": ULTIMO_DISPOSITIVO,
            "dentroDoHorario": dentro_do_horario(), "pausado": pausado(),
            "ultimoCicloSeg": None if _ULTIMO_CICLO is None else int(agora - _ULTIMO_CICLO),
            "contaSpotify": ({"ok": False, "motivo": self.conta_pendente} if self.conta_pendente
                             else {"ok": True}),
            "erros": [{"hora": hora, "haSeg": int(agora - t), "mensagem": mascarar(msg)}
                      for t, hora, msg in erros],
            "configErros": list(CONFIG_ERROS),
            "resultados": resultados,
        }

    # ---------- resposta do heartbeat ----------
    def receber(self, r):
        # config, comandos, carimbo e prazos. A config e os comandos que mexem no PC
        # ficam para o laco principal (na_thread_principal); este so guarda e acorda ele.
        agora = time.monotonic()
        servidor_ms = r.get("servidorMs") if _eh_int(r.get("servidorMs")) else None
        self.intervalo_hb = _faixa(r.get("intervaloHeartbeatSeg"), 60, 3600, self.intervalo_hb)
        self.consulta = _faixa(r.get("proximaConsultaSeg"), 15, 900, self.consulta)
        if "contaSpotifyId" in r and r.get("contaSpotifyId") != self.conta_em_uso:
            # conta do PC trocada ou removida no painel: o proximo pedido de token vai ao painel
            self.conta_trocou = True
        with self._trava:
            if isinstance(r.get("carimbo"), str):
                self.estado["carimbo"] = r["carimbo"]
            if isinstance(r.get("config"), dict):
                versao = r.get("versaoConfig") if _eh_int(r.get("versaoConfig")) else None
                self.estado.update(config=r["config"], versaoConfig=versao,
                                   contaSpotifyId=r.get("contaSpotifyId"))
                self.config_nova = (r["config"], versao, servidor_ms, agora)
                if versao != VERSAO_CONFIG_APLICADA:
                    _ACORDAR.set()
            self._receber_comandos(r.get("comandos"), servidor_ms, agora)
            self._gravar_estado()
        self._fazer_comandos_do_painel()

    def _receber_comandos(self, comandos, servidor_ms, agora):
        feitos = self.estado["comandosFeitos"]
        for c in comandos if isinstance(comandos, list) else []:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]:
                continue
            if c["id"] in feitos:
                continue  # ja recebido (mesmo antes de reiniciar): nunca repete
            feitos.append(c["id"])
            del feitos[:-50]
            idade = 0.0
            if servidor_ms is not None and _eh_int(c.get("criadoEm")):
                idade = max(0.0, (servidor_ms - c["criadoEm"]) / 1000.0)
            tipo_cmd = c.get("tipo")
            if tipo_cmd not in COMANDOS_DO_LACO + COMANDOS_DO_PAINEL:
                self._resultado(c["id"], False, "comando desconhecido")
            elif idade > PRAZO_COMANDO:
                self._resultado(c["id"], False, "comando expirado")
            else:
                parametros = c.get("parametros") if isinstance(c.get("parametros"), dict) else {}
                self.estado["comandosPendentes"].append(
                    {"id": c["id"], "tipo": tipo_cmd, "parametros": parametros,
                     "idadeSeg": idade, "recebido": agora})
                if tipo_cmd in COMANDOS_DO_LACO:
                    _ACORDAR.set()

    def _resultado(self, comando_id, ok, mensagem):
        pendentes = self.estado["resultadosPendentes"]
        pendentes.append({"comandoId": comando_id, "ok": bool(ok),
                          "mensagem": mascarar(str(mensagem))[:300]})
        del pendentes[:-50]
        self._pedido.set()  # resultado vai num heartbeat logo

    def resultado(self, comando_id, ok, mensagem):
        with self._trava:
            self._resultado(comando_id, ok, mensagem)
            self._gravar_estado()

    def _pegar(self, tipos):
        # Tira da fila (e do disco) ANTES de fazer: um reiniciar_guardian nunca roda 2 vezes.
        agora = time.monotonic()
        validos = []
        with self._trava:
            fila = self.estado["comandosPendentes"]
            pegos = [c for c in fila if c["tipo"] in tipos]
            if not pegos:
                return []
            self.estado["comandosPendentes"] = [c for c in fila if c["tipo"] not in tipos]
            for c in pegos:
                if c.get("idadeSeg", 0) + (agora - c.get("recebido", agora)) > PRAZO_COMANDO:
                    self._resultado(c["id"], False, "comando expirado")
                else:
                    validos.append(c)
            self._gravar_estado()
        return validos

    def pegar_comandos(self):
        return self._pegar(COMANDOS_DO_LACO)

    def pegar_config_nova(self):
        with self._trava:
            nova, self.config_nova = self.config_nova, None
        return nova

    def _fazer_comandos_do_painel(self):
        # sincronizar e enviar_log: so falam com o painel, feitos aqui mesmo
        for c in self._pegar(COMANDOS_DO_PAINEL):
            if c["tipo"] == "sincronizar":
                self.resultado(c["id"], True, "heartbeat enviado")
                continue
            parametros = c.get("parametros") if isinstance(c.get("parametros"), dict) else {}
            linhas = _faixa(parametros.get("linhas"), 1, 1000, 300)
            try:
                self.enviar_log(c["id"], linhas)
                self.resultado(c["id"], True, f"log enviado (ultimas {linhas} linhas)")
            except Exception as e:
                self.resultado(c["id"], False, f"nao consegui enviar o log: {resumo_erro(e)}")

    # ---------- thread do painel ----------
    def passo(self):
        # Faz o que estiver na hora (heartbeat ou novidades) e devolve quantos segundos
        # esperar ate a proxima vez. Nunca levanta erro: a thread nao pode morrer.
        agora = time.monotonic()
        na_hora_nov = self.com_novidades and agora >= self.proxima_nov
        if agora >= self.proximo_hb or na_hora_nov:
            try:
                if agora >= self.proximo_hb:
                    self._heartbeat_agendado()
                else:
                    if self.novidades():
                        self._heartbeat_agendado()
                    self.proxima_nov = time.monotonic() + self.consulta
                if self.falhas:
                    log("O painel voltou a responder.")
                    _LOG_LIMITADO.pop("painel", None)
                self.falhas = 0
            except NaoSuportado:
                self.com_novidades = False
                log(f"O painel nao tem a rota de novidades: so heartbeat a cada {self.intervalo_hb}s.")
            except TokenRevogado:
                tratar_revogacao()
                self._falhou(agora, None)
            except Exception as e:
                self._falhou(agora, e)
        alvo = min(self.proximo_hb, self.proxima_nov) if self.com_novidades else self.proximo_hb
        return max(0.0, alvo - time.monotonic())

    def _heartbeat_agendado(self):
        self.heartbeat()
        self.proximo_hb = time.monotonic() + self.intervalo_hb
        self.proxima_nov = time.monotonic() + self.consulta

    def _falhou(self, agora, erro):
        # espera crescente: 60, 120, 240, 480, 900, 900... (ou o tentarEmSeg do painel)
        self.falhas += 1
        espera = min(CONSULTA_NOVIDADES * 2 ** min(self.falhas - 1, 10), ESPERA_PAINEL_MAX)
        tentar_em = getattr(erro, "tentar_em", None)
        if _eh_int(tentar_em) or isinstance(tentar_em, float):
            espera = max(espera, min(int(tentar_em), ESPERA_PAINEL_MAX))
        if erro is not None:
            log_limitado("painel", f"Painel indisponivel ({resumo_erro(erro)}). "
                         f"Nova tentativa em {espera}s.")
        self.proxima_nov = agora + espera
        self.proximo_hb = max(self.proximo_hb, agora + espera)

    def laco(self):
        while True:
            if self._pedido.wait(self.passo()):
                self._pedido.clear()
                self.proximo_hb = 0.0  # resultado novo / sincronizar: heartbeat ja

    def iniciar(self):
        threading.Thread(target=self.laco, daemon=True).start()


class TokenDoPainel:
    # "auth_manager" do spotipy no modo painel: o access token do Spotify vem do painel
    # (POST /api/guardian/token), fica so em memoria e e renovado 10 min antes de vencer.
    # O spotipy 2.26 so chama get_access_token(as_dict=False) (client.py, _auth_headers).
    # Prazos so com o expiraEmSeg do painel e time.monotonic: o relogio do PC pode estar errado.

    def __init__(self, painel):
        self.painel = painel
        self._token = None
        self._renovar_em = self._vence_em = 0.0
        self._forcar = False
        self._trava = threading.Lock()

    def get_access_token(self, as_dict=False):
        with self._trava:
            if self.painel.conta_trocou:
                # a conta do PC mudou no painel: o token em cache e da conta antiga.
                # O /token devolve o da conta nova (ou 409 sem_conta -> ContaPendente).
                self.painel.conta_trocou = False
                self._token = None
            agora = time.monotonic()
            if self._token and agora < self._renovar_em:
                return self._token
            try:
                r = self.painel.token(self._forcar)
            except PainelIndisponivel:
                # painel fora do ar: segue com o token que tem ate faltar 1 min para vencer
                if self._token and agora < self._vence_em - 60:
                    return self._token
                raise
            token = r.get("accessToken")
            expira = _faixa(r.get("expiraEmSeg"), 0, 86400, 0)
            if not isinstance(token, str) or not token or expira <= 0:
                raise RespostaInvalida("/api/guardian/token: resposta sem accessToken/expiraEmSeg")
            agora = time.monotonic()
            self._token = token
            self._vence_em = agora + expira
            self._renovar_em = agora + max(expira - RENOVAR_TOKEN_ANTES, expira / 2)
            self._forcar = False
            return token

    def esquecer(self):
        # o Spotify recusou o token (401): o proximo e pedido ao painel com forcar=true
        with self._trava:
            self._token = None
            self._forcar = True


def aplicar_config_painel(config, versao=None, servidor_ms=None, recebido_em=None):
    # Aplica a ConfigPc do painel nas variaveis do guardian (so na thread principal).
    # So as chaves da lista do contrato, cada uma validada: valor errado mantem o anterior
    # e vai para configErros. As chaves que ficam no PC (startup_delay, fechar_popup,
    # url_atualizacao, atualizacao_atraso_s, credenciais) nunca vem do painel.
    # Devolve uma linha para o log se algo mudou (senao None).
    global PLAYLIST_URI, PLAYLIST_NOME, PLAYLIST_TOTAL, EMBARALHAR, HORARIO_INICIO, HORARIO_FIM
    global INTERVALO, DISPOSITIVO, ATUALIZACAO_AUTO, REABRIR_SOZINHO, PAUSADO_ATE
    global CONFIG_ERROS, VERSAO_CONFIG_APLICADA
    config = config if isinstance(config, dict) else {}
    erros = []

    def pegar(chave, padrao, valido, atual):
        valor = config.get(chave, padrao)
        if valido(valor):
            return valor
        erros.append(f"{chave} invalido: {str(valor)[:60]}")
        return atual

    def eh_hora(v):
        return v is None or (_eh_int(v) and 0 <= v <= 23)

    def eh_bool(v):
        return isinstance(v, bool)

    antes = (PLAYLIST_URI, PLAYLIST_NOME, EMBARALHAR, HORARIO_INICIO, HORARIO_FIM, INTERVALO,
             DISPOSITIVO, ATUALIZACAO_AUTO, REABRIR_SOZINHO, pausado())
    uri = config.get("playlistUri")
    playlist = None if uri is None else normalizar_playlist(uri) if isinstance(uri, str) else uri
    if playlist is not None and not (isinstance(playlist, str) and re.match(PLAYLIST_VALIDA, playlist)):
        erros.append(f"playlistUri invalido: {str(uri)[:60]}")
        playlist = PLAYLIST_URI
    if playlist != PLAYLIST_URI:
        PLAYLIST_TOTAL = None  # playlist nova: o tamanho e outro
    PLAYLIST_URI = playlist
    PLAYLIST_NOME = pegar("playlistNome", "", lambda v: v is None or isinstance(v, str), PLAYLIST_NOME) or ""
    # tamanho lido pela conta da TI: o PC sorteia a faixa sem ler uma playlist que nao e dele
    total = pegar("playlistTotal", None, lambda v: v is None or (_eh_int(v) and v >= 0), None)
    if total is not None:
        PLAYLIST_TOTAL = total
    EMBARALHAR = pegar("embaralhar", True, eh_bool, EMBARALHAR)
    HORARIO_INICIO = pegar("horarioInicio", None, eh_hora, HORARIO_INICIO)
    HORARIO_FIM = pegar("horarioFim", None, eh_hora, HORARIO_FIM)
    INTERVALO = pegar("intervaloVerificacaoSeg", 180, lambda v: _eh_int(v) and 60 <= v <= 3600, INTERVALO)
    DISPOSITIVO = (pegar("nomeDispositivo", "", lambda v: v is None or isinstance(v, str), DISPOSITIVO)
                   or "").strip()
    ATUALIZACAO_AUTO = pegar("atualizacaoAutomatica", True, eh_bool, ATUALIZACAO_AUTO)
    REABRIR_SOZINHO = pegar("reabrirSozinho", True, eh_bool, REABRIR_SOZINHO)
    pausa = pegar("pausadoAte", None, lambda v: v is None or _eh_int(v), None)
    if pausa is None or servidor_ms is None:
        PAUSADO_ATE = None  # sem servidorMs (config do cache) nao da pra saber quanto falta
    else:
        PAUSADO_ATE = (recebido_em or time.monotonic()) + (pausa - servidor_ms) / 1000.0
    versao = versao if _eh_int(versao) else None
    depois = (PLAYLIST_URI, PLAYLIST_NOME, EMBARALHAR, HORARIO_INICIO, HORARIO_FIM, INTERVALO,
              DISPOSITIVO, ATUALIZACAO_AUTO, REABRIR_SOZINHO, pausado())
    mudou = depois != antes or versao != VERSAO_CONFIG_APLICADA or erros != CONFIG_ERROS
    CONFIG_ERROS = erros
    VERSAO_CONFIG_APLICADA = versao
    if not mudou:
        return None
    if HORARIO_INICIO is None or HORARIO_FIM is None or HORARIO_INICIO == HORARIO_FIM:
        horario = "24h"
    else:
        horario = f"{HORARIO_INICIO}h-{HORARIO_FIM}h"
    linha = (f"Configuracao do painel (versao {versao}): playlist {PLAYLIST_URI or 'nenhuma'}"
             + (f" ({PLAYLIST_NOME})" if PLAYLIST_NOME else "")
             + f", horario {horario}, embaralhar {'sim' if EMBARALHAR else 'nao'}"
             + f", verificacao a cada {INTERVALO}s, aparelho {DISPOSITIVO or 'nome do computador'}"
             + (", PAUSADA pelo painel" if pausado() else "") + ".")
    if erros:
        linha += " ERROS: " + "; ".join(erros)
    return linha


def na_thread_principal(sp):
    # Modo painel: roda aqui o que a thread do painel recebeu (config nova e comandos),
    # para so o laco principal mexer nas variaveis e no Spotify. Legado: nao faz nada.
    if PAINEL is None:
        return
    _ACORDAR.clear()
    try:
        nova = PAINEL.pegar_config_nova()
        if nova:
            reabrir_antes = REABRIR_SOZINHO
            linha = aplicar_config_painel(*nova)
            if linha:
                log(linha, erro=bool(CONFIG_ERROS))
            if REABRIR_SOZINHO != reabrir_antes:
                try:
                    registrar_tarefa_vigia()  # o painel ligou/desligou o reabrirSozinho
                except Exception as e:
                    log(f"Nao consegui conferir a tarefa agendada: {resumo_erro(e)}", erro=True)
        for comando in PAINEL.pegar_comandos():
            executar_comando(sp, comando)
    except Exception as e:
        log(f"Erro ao tratar o que veio do painel: {resumo_erro(e)}", erro=True)


def executar_comando(sp, comando):
    # Comandos do painel que mexem no PC (feitos pelo laco principal).
    tipo_cmd, cid = comando["tipo"], comando["id"]
    log(f"Comando do painel: {tipo_cmd}.")
    try:
        if tipo_cmd == "reiniciar_guardian":
            # o resultado fica gravado antes: o guardian novo entrega no 1o heartbeat
            PAINEL.resultado(cid, True, "guardian reiniciado")
            reiniciar_processo("comando do painel")
            return
        if tipo_cmd == "reabrir_spotify":
            fechar_spotify()
            if not abrir_spotify():
                ok, mensagem = False, "nao consegui abrir o Spotify"
            else:
                time.sleep(10)
                if FECHAR_POPUP:
                    fechar_popup()
                ok, mensagem = True, "Spotify reaberto"  # a musica e conferida no ciclo
        elif tipo_cmd == "verificar_atualizacao":
            ok, mensagem = True, verificar_atualizacao(forcar=True)
        else:  # retomar_musica
            garantir_musica(sp)
            ok, mensagem = True, "musica conferida"
    except Exception as e:
        ok, mensagem = False, resumo_erro(e)
    PAINEL.resultado(cid, ok, mensagem)


def ciclo_concluido():
    # ultimo ciclo do laco principal que terminou sem erro (vai no heartbeat)
    global _ULTIMO_CICLO
    _ULTIMO_CICLO = time.monotonic()


def esperar_proximo_ciclo(espera, limite_do_spotify=False):
    # Modo legado: exatamente o time.sleep de sempre. Modo painel: acorda antes se o
    # painel mandar comando ou config nova, mas nunca encurta a espera de um 429.
    if PAINEL is None or limite_do_spotify:
        time.sleep(espera)
        return
    _ACORDAR.wait(espera)


def fechar_spotify():
    # Fecha o Spotify na marra (comando "reabrir_spotify" do painel).
    try:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "Spotify.exe"], creationflags=CREATE_NO_WINDOW,
                       timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"Nao consegui fechar o Spotify: {resumo_erro(e)}", erro=True)
    for _ in range(20):
        if spotify_aberto() is not True:
            return
        time.sleep(0.5)


def _soltar_mutex_guardian():
    global _MUTEX
    h, _MUTEX = _MUTEX, None
    if h:
        try:
            k32 = ctypes.WinDLL("kernel32")
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle(h)
        except Exception:
            pass


def reiniciar_processo(motivo):
    # Troca este guardian por um novo (comando do painel, revogacao, pareamento novo).
    # Segura o mutex de ajudante (a tarefa agendada nao abre outro no meio), solta o
    # do guardian, abre o novo e sai na hora, sem esperar as outras threads.
    log(f"Reiniciando o guardian ({motivo}).")
    _segurar_tarefa_vigia()
    _soltar_mutex_guardian()
    try:
        iniciar_em_segundo_plano()
        _esperar_guardian_subir(30)
    except Exception as e:
        log(f"Nao consegui abrir o guardian novo ({resumo_erro(e)}). A tarefa agendada "
            f"reabre em ate {INTERVALO_VIGIA_MIN} minutos.", erro=True)
    os._exit(0)


def modo_sem_conta():
    # Sem pareamento (e sem o config.json do modo antigo): segura o lugar (o mutex ja e
    # deste processo), mantem o Spotify aberto e espera o painel.json de um pareamento
    # ("Conectar ao painel" ou instalador). Quando ele aparece, reinicia no modo painel.
    if os.path.exists(ARQ_PAINEL):
        log("O painel.json deste PC nao pode ser lido (outro usuario do Windows ou arquivo "
            "copiado de outro PC). Conecte de novo: menu Iniciar > Spotify Guardian > "
            "'Conectar ao painel'.", erro=True)
    else:
        log("Este PC nao esta conectado ao painel Musica GEF: so mantem o Spotify aberto. "
            "Para conectar: menu Iniciar > Spotify Guardian > 'Conectar ao painel' "
            "(codigo de 6 digitos gerado no painel).")
    proxima_atualizacao = time.monotonic() + ATUALIZACAO_ATRASO
    while True:
        if ler_identidade():
            reiniciar_processo("PC conectado ao painel")
        if time.monotonic() >= proxima_atualizacao:
            proxima_atualizacao = time.monotonic() + INTERVALO_ATUALIZACAO
            try:
                verificar_atualizacao()
            except Exception as e:
                log(f"Nao consegui verificar atualizacao: {resumo_erro(e)}")
        try:
            if FECHAR_POPUP:
                fechar_popup()
            if spotify_aberto() is False and abrir_spotify():
                time.sleep(10)
        except Exception as e:
            log(f"Erro no loop: {resumo_erro(e)}", erro=True)
        time.sleep(ESPERA_SEM_CONTA)


def so_ascii(texto):
    # tira acentos e o que nao for ASCII (o instalador le a saida do --parear)
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\x20-\x7e]", "", texto).strip()


def parear_pc(codigo, url=None):
    # Troca o codigo de 6 digitos do painel pela identidade deste PC (painel.json).
    # Devolve (codigo de saida, mensagem, pcNome). O codigo e o token nunca vao para
    # a tela nem para o log.
    try:
        url = validar_url_painel(URL_PAINEL if url is None else url)
    except ValueError:
        return SAIDA_URL, "URL do painel invalida (precisa ser https://).", ""
    codigo = re.sub(r"[\s.-]", "", str(codigo or ""))
    if not re.match(r"^[0-9]{6}$", codigo):
        return SAIDA_FORMATO, "O codigo do painel tem 6 digitos (so numeros).", ""
    try:
        # o codigo so vale 1 vez: antes de gastar, confere que da para gravar aqui
        _dpapi(b"teste", True)
        _gravar_json_atomico(ARQ_PAINEL + ".teste", {})
        os.remove(ARQ_PAINEL + ".teste")
    except Exception as e:
        return SAIDA_GRAVACAO, f"Nao consigo gravar na pasta do guardian ({resumo_erro(e)}).", ""
    painel = Painel({"url": url})
    corpo = {"codigo": codigo, "nomePc": os.environ.get("COMPUTERNAME") or socket.gethostname(),
             "versaoGuardian": VERSAO, "windows": versao_windows(), "python": sys.version.split()[0]}
    try:
        r = painel._pedir("POST", "/api/guardian/parear", corpo)
    except RespostaInvalida as e:
        return SAIDA_RESPOSTA, f"Resposta inesperada do painel ({e}).", ""
    except PainelIndisponivel as e:
        if e.status == 400:
            return SAIDA_FORMATO, "O painel recusou o formato do codigo.", ""
        if e.status in (404, 409):
            return SAIDA_CODIGO, "Codigo invalido, vencido ou ja usado. Gere outro no painel.", ""
        if e.status is None or e.status == 429 or e.status >= 500:
            depois = f" Tente de novo em {e.tentar_em}s." if e.tentar_em else ""
            return SAIDA_REDE, f"Sem conexao com o painel ({e}).{depois}", ""
        return SAIDA_RESPOSTA, f"Resposta inesperada do painel ({e}).", ""
    except Exception as e:
        return SAIDA_RESPOSTA, f"Resposta inesperada do painel ({resumo_erro(e)}).", ""
    token, pc_id, pc_nome = r.get("token"), r.get("pcId"), r.get("pcNome")
    if (not isinstance(token, str) or not re.match(r"^[A-Za-z0-9_-]{20,200}$", token)
            or not isinstance(pc_id, str) or not pc_id or not isinstance(pc_nome, (str, type(None)))):
        return SAIDA_RESPOSTA, "Resposta do painel sem os dados do PC.", ""
    pc_nome = pc_nome or pc_id
    try:
        painel.estado = {"config": r["config"] if isinstance(r.get("config"), dict) else None,
                         "versaoConfig": r.get("versaoConfig") if _eh_int(r.get("versaoConfig")) else None,
                         "contaSpotifyId": r.get("contaSpotifyId"), "carimbo": None,
                         "comandosFeitos": [], "resultadosPendentes": [], "comandosPendentes": []}
        _gravar_json_atomico(ARQ_ESTADO_PAINEL, painel.estado)
        gravar_identidade(url, pc_id, pc_nome, token)
    except Exception as e:
        return SAIDA_GRAVACAO, f"Nao consegui gravar a identidade deste PC ({resumo_erro(e)}).", ""
    log(f"PC conectado ao painel {url} como '{pc_nome}'.")
    cfg = os.path.join(PASTA, "config.json")
    if not os.path.exists(cfg):
        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"startup_delay": 60}, f, indent=4)
        except OSError as e:
            log(f"Aviso: nao consegui criar o config.json ({resumo_erro(e)}).")
    try:
        # heartbeat de cortesia: o PC ja aparece online no painel antes de o guardian abrir
        painel.token_pc, painel.pc_nome = token, pc_nome
        painel.heartbeat()
    except Exception:
        pass
    return SAIDA_OK, f"PC conectado ao painel como '{pc_nome}'.", pc_nome


def _argumento_depois(opcao):
    if opcao in sys.argv:
        i = sys.argv.index(opcao)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    return None


def cli_parear():
    # guardian.py --parear CODIGO [--url URL] (instalador). Saida: RESULTADO=<n> e PCNOME=<nome>
    # A mensagem sai antes do log (o log tambem escreve no stdout): o instalador
    # le a 1a linha como a mensagem.
    resultado, mensagem, pc_nome = parear_pc(_argumento_depois("--parear"), _argumento_depois("--url"))
    print(mensagem)
    print(f"RESULTADO={resultado}")
    if resultado == SAIDA_OK:
        print("PCNOME=" + so_ascii(pc_nome))
    else:
        log(f"Pareamento com o painel nao deu certo: {mensagem}")
    return resultado


def conectar_painel():
    # Atalho "Conectar ao painel" do menu Iniciar: pede o codigo, pareia e reinicia o guardian.
    desligar_quickedit()
    print("=" * 55)
    print(" Conectar este PC ao painel Musica GEF")
    print(" (para cancelar, feche esta janela)")
    print("=" * 55)
    if IDENTIDADE:
        print(f"Este PC ja esta conectado como '{IDENTIDADE['pcNome']}'. Um codigo novo troca a conexao.")
    while True:
        codigo = input("\nCodigo de 6 digitos gerado no painel (Enter vazio para sair): ").strip()
        if not codigo:
            return
        resultado, mensagem, _ = parear_pc(codigo, _argumento_depois("--url"))
        print(mensagem)
        if resultado == SAIDA_OK:
            break
        if resultado in (SAIDA_GRAVACAO, SAIDA_URL):
            input("\nEnter pra fechar...")
            return
    reiniciar()


def desparear():
    # So para suporte: este PC esquece o painel (painel.json -> .revogado) e o guardian
    # reinicia no modo sem_conta, esperando um pareamento novo.
    desligar_quickedit()
    print("Desconectar este PC do painel Musica GEF (so para suporte).")
    if not os.path.exists(ARQ_PAINEL):
        print("Este PC nao esta conectado ao painel.")
        input("\nEnter pra fechar...")
        return
    if input("A musica para ate um novo pareamento. Digite S para confirmar: ").strip().upper() != "S":
        print("Cancelado.")
        input("\nEnter pra fechar...")
        return
    if not esquecer_identidade():
        print("ERRO: nao consegui desconectar (veja o guardian.log).")
        input("\nEnter pra fechar...")
        return
    log("PC desconectado do painel (--desparear).")
    reiniciar()


def orientar_relogin():
    # --relogin fora do modo legado: o login do Spotify agora e feito no painel
    if MODO == "painel":
        print("Este PC usa o painel Musica GEF: o login do Spotify e feito la.")
        print(f"Reconecte a conta no painel ({IDENTIDADE['url']}, Contas Spotify).")
    else:
        print("Este PC ainda nao esta conectado ao painel Musica GEF.")
        print("Use menu Iniciar > Spotify Guardian > 'Conectar ao painel'.")
    input("\nEnter pra fechar...")


def main():
    global PAINEL
    pela_tarefa = "--vigia" in sys.argv  # so chega aqui se o guardian NAO estava rodando
    config_cache = None
    if MODO == "painel":
        # a config do painel guardada em disco vale desde ja (inclusive o reabrirSozinho abaixo)
        PAINEL = Painel(IDENTIDADE)
        config, versao_cfg = PAINEL.config_em_cache()
        if config is not None:
            config_cache = aplicar_config_painel(config, versao_cfg)
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
    if MODO == "painel":
        log(f"Modo painel: {PAINEL.url} | PC '{PAINEL.pc_nome}'.")
        if config_cache:
            log(config_cache)
        elif PAINEL.config_em_cache()[0] is None:
            log("Ainda sem configuracao do painel: so mantem o Spotify aberto ate ela chegar.")
    elif MODO == "legado":
        if PLAYLIST_URI != PLAYLIST_CFG:
            log(f"playlist_uri do config.json ajustada para {PLAYLIST_URI}")
        if not re.match(r"^spotify:playlist:[0-9A-Za-z]{22}$", PLAYLIST_URI):
            log(f"ATENCAO: playlist_uri parece invalida: {PLAYLIST_URI!r}")
    if not URL_ATUALIZACAO:
        log("Atualizacao automatica desligada (sem repositorio configurado).")
    elif not ATUALIZACAO_AUTO:
        log("Atualizacao automatica desligada no " + ("painel." if MODO == "painel" else "config.json."))
    iniciar_vigia_de_antigos()
    if PAINEL is not None:
        PAINEL.iniciar()  # heartbeat ja: o PC aparece online durante o startup_delay
    if STARTUP_DELAY and not sem_espera:
        log(f"Aguardando {STARTUP_DELAY:g}s de inicializacao...")
        time.sleep(STARTUP_DELAY)
    if MODO == "sem_conta":
        modo_sem_conta()
        return

    sp = conectar()
    falhas_rede = 0
    proxima_atualizacao = time.monotonic() + ATUALIZACAO_ATRASO

    while True:
        na_thread_principal(sp)  # modo painel: config nova e comandos (legado: nada)
        espera = INTERVALO
        limite_do_spotify = False
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
            ciclo_concluido()
        except ContaPendente as e:
            # modo painel: a conta Spotify deste PC nao tem login valido no painel
            espera = ESPERA_CONTA_PENDENTE
            log_limitado("conta_pendente", f"A conta Spotify deste PC precisa de atencao no painel "
                         f"({e.motivo}): reconecte a conta no painel. Conferindo de novo a cada "
                         f"{ESPERA_CONTA_PENDENTE // 60} min.")
        except TokenRevogado:
            tratar_revogacao()
        except requests.exceptions.RequestException as e:
            # Queda de internet / conexao derrubada: tenta de novo logo.
            # (painel fora do ar, no modo painel, tambem cai aqui: PainelIndisponivel)
            falhas_rede += 1
            if falhas_rede <= MAX_FALHAS_RAPIDAS:
                espera = ESPERA_FALHA_REDE
            log(f"Falha de rede ({falhas_rede}a seguida): {resumo_erro(e)}. "
                f"Nova tentativa em {espera}s.", erro=True)
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
                limite_do_spotify = True  # nem comando do painel encurta esta espera
                log(f"Limite de requisicoes atingido. Aguardando {espera}s antes de tentar de novo.",
                    erro=True)
            elif status and status >= 500:
                espera = 60
                log(f"Spotify instavel ({resumo_erro(e)}). Nova tentativa em {espera}s.", erro=True)
            elif status == 401 and PAINEL is not None:
                # modo painel: o Spotify recusou o token; pede outro ao painel (forcar) e tenta logo
                sp.auth_manager.esquecer()
                falhas_rede += 1
                if falhas_rede <= MAX_FALHAS_RAPIDAS:
                    espera = ESPERA_FALHA_REDE
                log(f"O Spotify recusou o token ({resumo_erro(e)}). Pedindo outro ao painel; "
                    f"nova tentativa em {espera}s.", erro=True)
            else:
                log(f"Erro Spotify: {resumo_erro(e)}", erro=True)
        except SpotifyOauthError as e:
            if getattr(e, "error", None) == "invalid_grant" or "invalid_grant" in str(e):
                espera = 1800
                log("LOGIN DO SPOTIFY EXPIRADO (o Spotify exige novo login a cada 6 meses). "
                    "Use menu Iniciar > Spotify Guardian > 'Refazer login do Spotify'.", erro=True)
            elif login_pendente(e):
                espera = 1800
                log("Login do Spotify pendente: ninguem entrou com a conta no navegador "
                    "(ou o login foi cancelado). "
                    "Use menu Iniciar > Spotify Guardian > 'Refazer login do Spotify'.", erro=True)
            else:
                log(f"Erro no login do Spotify: {e}", erro=True)
        except Exception as e:
            log(f"Erro no loop: {resumo_erro(e)}", erro=True)
        esperar_proximo_ciclo(espera, limite_do_spotify)


if __name__ == "__main__":
    if "--parear" in sys.argv:
        sys.exit(cli_parear())
    elif "--conectar-painel" in sys.argv:
        conectar_painel()
    elif "--desparear" in sys.argv:
        desparear()
    elif "--relogin" in sys.argv:
        if MODO == "legado":
            refazer_login()
        else:
            orientar_relogin()
    elif "--reiniciar" in sys.argv:
        reiniciar()
    else:
        main()
