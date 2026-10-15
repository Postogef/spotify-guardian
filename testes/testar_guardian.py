"""
Testes offline do guardian.py. Roda no Python embutido (3.8 e 3.14) pelo build.ps1:
    python testar_guardian.py <caminho do guardian.py>

Nada sai para a internet: qualquer conexao fora de 127.0.0.1 e bloqueada
(proxy invalido). Nao abre o Spotify, nao fecha janelas, nao roda instalador.
"""
import os
import re
import sys
import json
import time
import base64
import shutil
import hashlib
import tempfile
import threading
import subprocess
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer

# bloqueia internet antes de importar requests/spotipy
os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

ORIGEM = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                         os.path.join(os.path.dirname(__file__), "..", "src", "guardian.py"))
PASTA = tempfile.mkdtemp(prefix="guardian_teste_")
shutil.copy(ORIGEM, os.path.join(PASTA, "guardian.py"))
with open(os.path.join(PASTA, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"client_id": "cid", "client_secret": "cs",
               "redirect_uri": "http://127.0.0.1:8888/callback",
               "playlist_uri": "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt?si=abc",
               "embaralhar": True, "check_interval": 180, "startup_delay": 0,
               "horario_inicio": None, "horario_fim": None}, f)

spec = importlib.util.spec_from_file_location("guardian", os.path.join(PASTA, "guardian.py"))
g = importlib.util.module_from_spec(spec)
sys.modules["guardian"] = g
spec.loader.exec_module(g)

import requests
import spotipy
from spotipy.oauth2 import SpotifyOauthError

SE = spotipy.SpotifyException
RESULTADOS = []


def teste(fn):
    RESULTADOS.append(fn)
    return fn


def perigo(nome):
    def f(*a, **k):
        raise AssertionError("chamou %s() de verdade" % nome)
    return f


g.print = lambda *a, **k: None  # log() continua gravando no guardian.log de teste
g._schtasks = perigo("_schtasks")  # nenhum teste pode mexer no Agendador de Tarefas de verdade
g.fechar_popup = perigo("fechar_popup")
g.abrir_spotify = perigo("abrir_spotify")
g.spotify_aberto = lambda: True
g.desligar_quickedit = lambda: None


class Pare(BaseException):
    pass


class RelogioFalso:
    def __init__(self, parar_depois=None):
        self.esperas = []
        self.parar_depois = parar_depois

    def __call__(self, s):
        self.esperas.append(s)
        if self.parar_depois is not None and len(self.esperas) >= self.parar_depois:
            raise Pare()


class SpotifyFalso:
    """Responde por metodo: valor, excecao, ou lista (uma resposta por chamada)."""

    def __init__(self, **respostas):
        self.r = {"current_playback": None, "devices": {"devices": []},
                  "playlist_items": {"total": 50}, "start_playback": None,
                  "shuffle": None, "transfer_playback": None}
        self.r.update(respostas)
        self.chamadas = []

    def _faz(self, nome, a, k):
        self.chamadas.append((nome, a, k))
        v = self.r[nome]
        if isinstance(v, list):
            v = v.pop(0) if len(v) > 1 else v[0]
        if isinstance(v, BaseException):
            raise v
        return v

    def __getattr__(self, nome):
        if nome in ("current_playback", "devices", "playlist_items", "start_playback",
                    "shuffle", "transfer_playback"):
            return lambda *a, **k: self._faz(nome, a, k)
        raise AttributeError(nome)

    def nomes(self):
        return [c[0] for c in self.chamadas]


PC = {"id": "pc-loja", "name": "LOJA-PC", "type": "Computer", "is_active": False, "is_restricted": False}
CEL = {"id": "cel", "name": "iPhone", "type": "Smartphone", "is_active": True, "is_restricted": False}


def reset():
    g.PLAYLIST_TOTAL = None
    g.DISPOSITIVO = ""
    g.nomes_deste_pc = lambda: {"loja-pc"}
    g.time.sleep = RelogioFalso()


# ------------------------------------------------------------ basicos
@teste
def t_playlist_normalizada_no_import():
    assert g.PLAYLIST_URI == "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt", g.PLAYLIST_URI


@teste
def t_normalizar_playlist():
    casos = {
        "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt?si=x": "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt",
        "https://open.spotify.com/intl-pt/playlist/4p3LjEkrRzUVQeCOmiLDSt?si=a&pi=b": "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt",
        " 4p3LjEkrRzUVQeCOmiLDSt ": "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt",
        "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX": "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX",
    }
    for entrada, esperado in casos.items():
        assert g.normalizar_playlist(entrada) == esperado, (entrada, g.normalizar_playlist(entrada))


@teste
def t_ler_config_encodings():
    obj = {"client_id": "x", "dispositivo": "ESTAÇÃO"}
    arq = os.path.join(PASTA, "cfg_teste.json")
    texto = json.dumps(obj, ensure_ascii=False)
    for dados in (texto.encode("utf-8"), b"\xef\xbb\xbf" + texto.encode("utf-8"),
                  texto.encode("cp1252"), texto.encode("utf-16")):
        with open(arq, "wb") as f:
            f.write(dados)
        assert g.ler_config(arq) == obj


@teste
def t_horario():
    class DT:
        hora = 0

        @classmethod
        def now(cls):
            class N:
                hour = cls.hora
            return N()
    original = g.datetime
    g.datetime = DT
    try:
        for ini, fim, dentro, fora in ((8, 22, [8, 21], [7, 22]), (18, 2, [18, 23, 0, 1], [2, 17]),
                                       (5, 5, [0, 5, 23], []), (None, None, [3], [])):
            g.HORARIO_INICIO, g.HORARIO_FIM = ini, fim
            for h in dentro:
                DT.hora = h
                assert g.dentro_do_horario(), (ini, fim, h)
            for h in fora:
                DT.hora = h
                assert not g.dentro_do_horario(), (ini, fim, h)
    finally:
        g.datetime = original
        g.HORARIO_INICIO = g.HORARIO_FIM = None


@teste
def t_versao_tupla():
    assert g.versao_tupla("v2.0.10") > g.versao_tupla("2.0.9")
    assert g.versao_tupla("v2.0.0") == (2, 0, 0)
    assert g.versao_tupla("") == (0,)


@teste
def t_tarefa_dispara_mesmo_com_relogio_atrasado():
    # PC com a pilha da placa fraca liga com data antiga: o gatilho nao pode comecar "no futuro"
    assert "<StartBoundary>2000-01-01T00:00:00</StartBoundary>" in g.xml_tarefa_vigia()


@teste
def t_instancia_unica():
    # nome proprio do teste: nao depende de haver um guardian de verdade rodando
    g.MUTEX_NOME = "Local\\SpotifyGuardianTeste-%d" % os.getpid()
    assert g.guardian_rodando() is False
    assert g.ja_esta_rodando() is False
    assert g.ja_esta_rodando() is True  # o mesmo nome de novo = ja existe
    assert g.guardian_rodando() is True


@teste
def t_prazo_no_login_do_navegador():
    # o servidor local do login desiste sozinho e fecha a porta
    import spotipy.oauth2 as oauth2
    original = g.PRAZO_LOGIN
    g.PRAZO_LOGIN = 0.2
    try:
        servidor = oauth2.start_local_http_server(0)
        assert servidor.timeout == 0.2
        inicio = time.monotonic()
        servidor.handle_request()
        assert time.monotonic() - inicio < 5
        assert servidor.socket.fileno() == -1  # porta fechada
    finally:
        g.PRAZO_LOGIN = original
    assert g.login_pendente(SpotifyOauthError("Server listening on localhost has not been accessed"))
    assert g.login_pendente(SpotifyOauthError("Received error from auth server: access_denied"))
    assert not g.login_pendente(SpotifyOauthError("x", error="invalid_grant"))


@teste
def t_janelas_de_ajuda_ignoram_ctrl_c():
    # Ctrl+C no meio do "Reiniciar"/"Refazer login" deixava um parar.ps1 solto
    import signal
    anterior = signal.getsignal(signal.SIGINT)
    try:
        g._ignorar_ctrl_c()
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, anterior)


@teste
def t_reabrir_cancela_parar_em_andamento():
    # janela fechada no meio do parar.ps1: ele e cancelado ANTES de abrir o guardian novo
    import subprocess
    lento = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    abertos = []
    original = g.iniciar_em_segundo_plano
    g.iniciar_em_segundo_plano = lambda: abertos.append(lento.poll())
    g._PARAR_EM_CURSO.append(lento)
    try:
        reabrir = g._reabridor()
        reabrir()
        reabrir()  # so reabre uma vez
    finally:
        g.iniciar_em_segundo_plano = original
        if lento in g._PARAR_EM_CURSO:
            g._PARAR_EM_CURSO.remove(lento)
        del g._FECHANDO[:]
        if lento.poll() is None:
            lento.kill()
    assert len(abertos) == 1 and abertos[0] is not None, abertos  # ja estava morto ao reabrir


# ------------------------------------------------------------ aparelhos
@teste
def t_escolhe_pc_pelo_nome():
    reset()
    sp = SpotifyFalso(devices={"devices": [CEL, PC]})
    assert g.escolher_dispositivo(sp)["id"] == "pc-loja"


@teste
def t_nunca_celular():
    reset()
    sp = SpotifyFalso(devices={"devices": [CEL]})
    assert g.escolher_dispositivo(sp) is None


@teste
def t_dispositivo_do_config_e_estrito():
    reset()
    g.DISPOSITIVO = "Caixa 1"
    sp = SpotifyFalso(devices={"devices": [PC]})
    assert g.escolher_dispositivo(sp) is None
    g.DISPOSITIVO = ""


@teste
def t_devices_429_sobe_para_o_main():
    reset()
    sp = SpotifyFalso(devices=SE(429, -1, "limite", headers={"Retry-After": "600"}))
    try:
        g.escolher_dispositivo(sp)
        raise AssertionError("deveria subir o 429")
    except SE as e:
        assert e.http_status == 429
    assert sp.nomes() == ["devices"]


# ------------------------------------------------------------ garantir_musica
@teste
def t_tocando_nao_faz_nada():
    reset()
    sp = SpotifyFalso(current_playback={"is_playing": True, "device": PC})
    g.garantir_musica(sp)
    assert sp.nomes() == ["current_playback"]


@teste
def t_parado_inicia_playlist_no_pc():
    reset()
    sp = SpotifyFalso(current_playback={"is_playing": False, "device": {"id": "velho"}},
                      devices={"devices": [PC]})
    g.garantir_musica(sp)
    nome, a, k = [c for c in sp.chamadas if c[0] == "start_playback"][0]
    assert k["device_id"] == "pc-loja" and k["context_uri"] == "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt"
    assert 0 <= k["offset"]["position"] < 50
    assert "shuffle" in sp.nomes()


@teste
def t_404_recupera_com_transfer():
    reset()
    sp = SpotifyFalso(devices={"devices": [PC]},
                      start_playback=[SE(404, -1, "Not found."), None])
    g.garantir_musica(sp)
    n = sp.nomes()
    assert n.count("start_playback") == 2 and "transfer_playback" in n, n
    ultimo = [c for c in sp.chamadas if c[0] == "start_playback"][-1]
    assert ultimo[2]["offset"] is None
    transf = [c for c in sp.chamadas if c[0] == "transfer_playback"][0]
    assert transf[2].get("force_play") is False


@teste
def t_404_duas_vezes_sobe_erro():
    reset()
    sp = SpotifyFalso(devices={"devices": [PC]}, start_playback=SE(404, -1, "Not found."))
    try:
        g.garantir_musica(sp)
        raise AssertionError("deveria subir o 404")
    except SE as e:
        assert e.http_status == 404


# ------------------------------------------------------------ guardian antigo de outra pasta
@teste
def t_parar_guardians_antigos_le_a_contagem():
    script = os.path.join(PASTA, "parar.ps1")
    if os.path.exists(script):
        os.remove(script)
    assert g.parar_guardians_antigos() is None  # sem parar.ps1 (pasta antiga): nao faz nada

    class R:
        stdout = b"PARADOS=2\r\n"
    chamadas = []
    original = g.subprocess.run
    open(script, "w").close()
    try:
        g.subprocess.run = lambda args, **k: chamadas.append(args) or R
        assert g.parar_guardians_antigos() == 2
        R.stdout = b""
        assert g.parar_guardians_antigos() is None
    finally:
        g.subprocess.run = original
        os.remove(script)
    args = chamadas[0]
    assert "-SoAntigos" in args and args[args.index("-Exceto") + 1] == str(os.getpid())
    assert args[args.index("-Pasta") + 1] == g.PASTA


@teste
def t_vigia_anota_quando_fecha_antigo():
    respostas = [1, RuntimeError("falhou"), 0]

    def falso():
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    originais = (g.parar_guardians_antigos, g.ATRASOS_VIGIA_ANTIGOS)
    g.parar_guardians_antigos, g.ATRASOS_VIGIA_ANTIGOS = falso, (0, 0, 0)
    try:
        g._vigiar_guardians_antigos()  # nao pode levantar erro
    finally:
        g.parar_guardians_antigos, g.ATRASOS_VIGIA_ANTIGOS = originais
    assert respostas == []
    with open(g.LOG_FILE, encoding="utf-8") as f:
        texto = f.read()
    assert texto.count("Fechei 1 Spotify Guardian antigo") == 1
    assert "Nao consegui conferir se ha guardian antigo rodando" in texto


# ------------------------------------------------------------ tarefa agendada (reabre sozinho)
class _Saida:
    def __init__(self, codigo, texto=b""):
        self.returncode, self.stdout = codigo, texto


def _com_schtasks_falso(respostas):
    """Troca g._schtasks por um falso que devolve as respostas em ordem; devolve a lista de chamadas."""
    chamadas = []

    def falso(*args):
        chamadas.append(args)
        if args[0] == "/Create":  # confere o XML que seria registrado
            with open(args[args.index("/XML") + 1], encoding="utf-16") as f:
                chamadas.append(("XML", f.read()))
        return respostas.pop(0)
    g._schtasks = falso
    return chamadas


def _instalacao_falsa():
    # registrar_tarefa_vigia so age na instalacao do instalador: pythonw.exe + parar.ps1
    open(os.path.join(PASTA, "parar.ps1"), "w").close()
    original = g.sys.executable
    pasta_py = os.path.join(PASTA, "python")
    os.makedirs(pasta_py, exist_ok=True)
    open(os.path.join(pasta_py, "pythonw.exe"), "w").close()
    g.sys.executable = os.path.join(pasta_py, "python.exe")
    return original


@teste
def t_tarefa_vigia_cria_confere_e_remove():
    originais = (g._schtasks, g.REABRIR_SOZINHO)
    executavel = _instalacao_falsa()
    if os.path.exists(g.ARQ_TAREFA):
        os.remove(g.ARQ_TAREFA)
    try:
        # 1) nao existe -> cria
        chamadas = _com_schtasks_falso([_Saida(1), _Saida(0)])
        assert g.registrar_tarefa_vigia() == "criada"
        assert chamadas[0][0] == "/Query" and chamadas[1][0] == "/Create" and "/F" in chamadas[1]
        xml = chamadas[2][1]
        assert "<Interval>PT5M</Interval>" in xml and "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
        assert "pythonw.exe</Command>" in xml and 'guardian.py" --vigia</Arguments>' in xml
        assert "<LogonType>InteractiveToken</LogonType>" in xml and "LeastPrivilege" in xml
        assert not os.path.exists(os.path.join(tempfile.gettempdir(), "spotify_guardian_tarefa.xml"))
        # 2) existe, esta ligada, aponta para esta pasta e nao mudou -> nao mexe
        from xml.sax.saxutils import escape
        registrada = ('<Enabled>true</Enabled><Arguments>"%s\\guardian.py" --vigia</Arguments>'
                      % escape(PASTA)).encode("oem", errors="replace")
        chamadas = _com_schtasks_falso([_Saida(0, registrada)])
        assert g.registrar_tarefa_vigia() == "ok" and len(chamadas) == 1
        assert chamadas[0][-1] == "/XML"
        # 2b) alguem desativou a tarefa no Agendador -> refaz
        chamadas = _com_schtasks_falso([_Saida(0, registrada.replace(b">true<", b">false<")), _Saida(0)])
        assert g.registrar_tarefa_vigia() == "criada" and chamadas[1][0] == "/Create"
        if PASTA.isascii():  # com acento no caminho o guardian nao compara a pasta (a saida do schtasks vem em OEM)
            # 2c) tarefa de outra pasta (instalacao movida) -> refaz
            chamadas = _com_schtasks_falso([_Saida(0, b'<Arguments>"C:\\outra\\guardian.py" --vigia</Arguments>'), _Saida(0)])
            assert g.registrar_tarefa_vigia() == "criada"
            # 2d) tarefa de uma pasta vizinha de nome parecido -> refaz
            vizinha = registrada.replace(b"\\guardian.py", b" Copia\\guardian.py")
            chamadas = _com_schtasks_falso([_Saida(0, vizinha), _Saida(0)])
            assert g.registrar_tarefa_vigia() == "criada" and chamadas[1][0] == "/Create"
        # 3) foi apagada por fora -> recria
        chamadas = _com_schtasks_falso([_Saida(1), _Saida(0)])
        assert g.registrar_tarefa_vigia() == "criada"
        # 4) Windows recusou -> so anota no log, sem erro
        os.remove(g.ARQ_TAREFA)
        chamadas = _com_schtasks_falso([_Saida(1), _Saida(1, b"ERRO: Acesso negado.")])
        assert g.registrar_tarefa_vigia() is None
        # 5) desligada no config -> remove
        g.REABRIR_SOZINHO = False
        chamadas = _com_schtasks_falso([_Saida(0), _Saida(0)])
        assert g.registrar_tarefa_vigia() == "removida" and chamadas[1][0] == "/Delete"
        chamadas = _com_schtasks_falso([_Saida(1)])
        assert g.registrar_tarefa_vigia() is None and len(chamadas) == 1
    finally:
        g._schtasks, g.REABRIR_SOZINHO = originais
        g.sys.executable = executavel
        os.remove(os.path.join(PASTA, "parar.ps1"))
    with open(g.LOG_FILE, encoding="utf-8") as f:
        assert "Acesso negado" in f.read()


@teste
def t_tarefa_vigia_nao_age_fora_da_instalacao():
    # rodando de uma pasta sem parar.ps1 (desenvolvimento / pasta antiga): nao cria tarefa
    original = g._schtasks
    chamadas = _com_schtasks_falso([])
    try:
        assert g.registrar_tarefa_vigia() is None and chamadas == []
    finally:
        g._schtasks = original


@teste
def t_xml_da_tarefa_escapa_caracteres():
    originais = (g.PASTA, dict(os.environ))
    g.PASTA = r"C:\Users\TELAO & GEF 1\AppData\Local\SpotifyGuardian"
    os.environ["USERNAME"], os.environ["USERDOMAIN"] = "TELAO & GEF 1", "LOJA<1>"
    try:
        definicao = g.xml_tarefa_vigia()
    finally:
        g.PASTA = originais[0]
        os.environ.clear()
        os.environ.update(originais[1])
    from xml.dom import minidom
    doc = minidom.parseString(definicao.split("?>", 1)[1])  # XML valido mesmo com & e < no nome
    texto = lambda tag: doc.getElementsByTagName(tag)[0].firstChild.data
    assert texto("UserId") == "LOJA<1>\\TELAO & GEF 1"
    assert texto("Arguments") == '"C:\\Users\\TELAO & GEF 1\\AppData\\Local\\SpotifyGuardian\\guardian.py" --vigia'
    assert texto("WorkingDirectory") == r"C:\Users\TELAO & GEF 1\AppData\Local\SpotifyGuardian"


@teste
def t_vigia_sai_calado_se_o_guardian_ja_roda():
    # o que a tarefa agendada faz a cada 5 min quando esta tudo bem: nada, e sem log
    import ctypes
    import subprocess
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    with open(g.LOG_FILE, encoding="utf-8") as f:
        antes = f.read()
    # os nomes de verdade: sao os que o processo filho confere. Vale para o guardian
    # rodando e para a janela "Refazer login"/"Reiniciar" trabalhando.
    for nome in ("Local\\SpotifyGuardianGEF", g.MUTEX_AJUDANTE):
        h = k32.CreateMutexW(None, False, nome)
        try:
            assert g._mutex_existe(nome)  # sem isso o filho viraria um guardian de verdade
            r = subprocess.run([sys.executable, os.path.join(PASTA, "guardian.py"), "--vigia"],
                               timeout=60, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert r.returncode == 0 and r.stdout == b"" and r.stderr == b"", (nome, r.returncode, r.stderr)
        finally:
            k32.CloseHandle(h)
        assert not g._mutex_existe(nome) or nome == "Local\\SpotifyGuardianGEF"
    with open(g.LOG_FILE, encoding="utf-8") as f:
        assert f.read() == antes  # nenhuma linha nova no log


@teste
def t_janela_de_ajuda_segura_e_solta_a_tarefa():
    assert not g._mutex_existe(g.MUTEX_AJUDANTE)
    g._segurar_tarefa_vigia()
    assert g._mutex_existe(g.MUTEX_AJUDANTE)
    g._soltar_tarefa_vigia()
    assert not g._mutex_existe(g.MUTEX_AJUDANTE)
    g._soltar_tarefa_vigia()  # soltar duas vezes nao da erro


# ------------------------------------------------------------ main (tratamento de erros)
def rodar_main(erro, n=1):
    reset()
    relogio = RelogioFalso(parar_depois=n)
    g.time.sleep = relogio
    g.ATUALIZACAO_ATRASO = 10 ** 9
    originais = (g.garantir_musica, g.conectar, g.ja_esta_rodando, g.iniciar_vigia_de_antigos)

    def falha(sp):
        raise erro
    g.garantir_musica = falha
    g.conectar = lambda: object()
    g.ja_esta_rodando = lambda: False
    g.iniciar_vigia_de_antigos = lambda: None
    try:
        g.main()
    except Pare:
        pass
    finally:
        g.garantir_musica, g.conectar, g.ja_esta_rodando, g.iniciar_vigia_de_antigos = originais
    return relogio.esperas


@teste
def t_main_falha_de_rede_tenta_logo():
    esperas = rodar_main(requests.exceptions.ReadTimeout("Read timed out."), n=5)
    assert esperas == [20, 20, 20, 180, 180], esperas


@teste
def t_main_429_respeita_retry_after():
    assert rodar_main(SE(429, -1, "x", headers={"Retry-After": "42"})) == [60]
    assert rodar_main(SE(429, -1, "x", headers={"Retry-After": "7200"})) == [3600]
    assert rodar_main(SE(429, -1, "x", headers={})) == [900]


@teste
def t_main_5xx_espera_60():
    assert rodar_main(SE(503, -1, "x")) == [60]


@teste
def t_main_login_expirado():
    assert rodar_main(SpotifyOauthError("x", error="invalid_grant")) == [1800]


@teste
def t_main_login_pendente():
    assert rodar_main(SpotifyOauthError("Server listening on localhost has not been accessed")) == [1800]


@teste
def t_minutos_ligado_e_o_menor_entre_pc_e_sessao():
    import ctypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetTickCount64.restype = ctypes.c_ulonglong
    pc = k32.GetTickCount64() / 60000.0
    sessao = g._minutos_de_sessao()
    assert sessao >= 0
    if os.environ.get("SESSIONNAME"):  # rodando na tela de um usuario: o Windows sabe quando ele entrou
        assert sessao > 0
    assert 0 <= g._minutos_ligado() <= pc + 1
    original = g._minutos_de_sessao
    try:
        # ligou agora com a "Inicializacao rapida" do Windows 10: o tempo do PC nao zera
        g._minutos_de_sessao = lambda: 0.5
        assert g._minutos_ligado() <= 0.5
        g._minutos_de_sessao = lambda: 10 ** 9
        assert abs(g._minutos_ligado() - pc) < 1
    finally:
        g._minutos_de_sessao = original


@teste
def t_vigia_espera_o_startup_delay_so_com_o_pc_recem_ligado():
    originais = (list(sys.argv), g.STARTUP_DELAY, g._minutos_ligado)
    sys.argv[:] = [sys.argv[0], "--vigia"]
    g.STARTUP_DELAY = 7
    try:
        g._minutos_ligado = lambda: 3  # acabou de ligar: espera, como o atalho da inicializacao
        assert rodar_main(SE(503, -1, "x")) == [7]
        g._minutos_ligado = lambda: 6  # fechado no meio do dia: reabre direto
        assert rodar_main(SE(503, -1, "x")) == [60]
    finally:
        sys.argv[:], g.STARTUP_DELAY, g._minutos_ligado = originais


@teste
def t_vigia_com_reabrir_sozinho_desligado_so_remove_a_tarefa():
    originais = (list(sys.argv), g.REABRIR_SOZINHO, g.registrar_tarefa_vigia, g.ja_esta_rodando)
    chamadas = []
    sys.argv[:] = [sys.argv[0], "--vigia"]
    g.REABRIR_SOZINHO = False
    g.registrar_tarefa_vigia = lambda: chamadas.append("registrar")
    g.ja_esta_rodando = perigo("ja_esta_rodando")
    try:
        g.main()  # volta sem virar guardian
    finally:
        sys.argv[:], g.REABRIR_SOZINHO, g.registrar_tarefa_vigia, g.ja_esta_rodando = originais
    assert chamadas == ["registrar"]


# ------------------------------------------------------------ servidor local (API falsa)
class _HTTPServerCalado(HTTPServer):
    def handle_error(self, request, client_address):
        pass  # cliente que desistiu (teste de timeout): nada de traceback na tela


class Servidor:
    """Rota -> (status, cabecalhos, corpo) ou funcao(detalhe) que devolve isso.
    O corpo pode ser bytes, dict/list (vira JSON) ou funcao sem argumentos.
    Guarda cada pedido em `detalhes` (metodo, caminho, cabecalhos em minusculas, corpo lido)."""

    def __init__(self, rotas):
        self.rotas = rotas
        self.pedidos = []
        self.detalhes = []
        servidor = self

        class H(BaseHTTPRequestHandler):
            def _responder(self):
                servidor.pedidos.append((self.command, self.path))
                tamanho = int(self.headers.get("Content-Length") or 0)
                bruto = self.rfile.read(tamanho) if tamanho else b""
                try:
                    lido = json.loads(bruto.decode("utf-8")) if bruto else None
                except ValueError:
                    lido = bruto
                detalhe = {"metodo": self.command, "caminho": self.path.split("?")[0],
                           "cabecalhos": {k.lower(): v for k, v in self.headers.items()}, "corpo": lido}
                servidor.detalhes.append(detalhe)
                resposta = servidor.rotas.get(detalhe["caminho"], (404, {}, b"{}"))
                if callable(resposta):
                    resposta = resposta(detalhe)
                status, cabecalhos, corpo = resposta
                if callable(corpo):
                    corpo = corpo()
                if not isinstance(corpo, bytes):
                    corpo = json.dumps(corpo).encode("utf-8")
                self.send_response(status)
                for k, v in cabecalhos.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
            do_GET = do_PUT = do_POST = do_DELETE = _responder

            def log_message(self, *a):
                pass

        self.httpd = _HTTPServerCalado(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def fechar(self):
        self.httpd.shutdown()


@teste
def t_conexao_nova_e_status_reais():
    # 429 e 503 chegam com o status e o Retry-After de verdade
    s = Servidor({"/v1/me/player": (429, {"Retry-After": "42"}, b'{"error":{"status":429,"message":"x"}}')})
    try:
        sp = g.conectar()
        sp.prefix = s.url + "/v1/"
        sp.auth_manager.get_access_token = lambda *a, **k: "token"
        try:
            sp.current_playback()
            raise AssertionError("deveria dar 429")
        except SE as e:
            assert e.http_status == 429 and e.headers.get("Retry-After") == "42", (e.http_status, e.headers)
        assert sp._session is requests.api and sp.auth_manager._session is requests.api
    finally:
        s.fechar()


# ------------------------------------------------------------ atualizacao automatica
def servidor_release(tag, setup=b"MZ instalador falso", sha=None, assets=True, prerelease=False):
    sha = sha if sha is not None else hashlib.sha256(setup).hexdigest()
    rotas = {}
    s = Servidor(rotas)
    rel = {"tag_name": tag, "draft": False, "prerelease": prerelease, "assets": []}
    if assets:
        rel["assets"] = [
            {"name": "SpotifyGuardian-Setup.exe", "browser_download_url": s.url + "/d/setup.exe"},
            {"name": "SpotifyGuardian-Setup.exe.sha256", "browser_download_url": s.url + "/d/setup.sha256"},
        ]
    rotas["/repos/x/y/releases/latest"] = (200, {"Content-Type": "application/json"}, json.dumps(rel).encode())
    rotas["/d/setup.exe"] = (200, {}, setup)
    rotas["/d/setup.sha256"] = (200, {}, (sha + "  SpotifyGuardian-Setup.exe\n").encode())
    return s


def rodar_atualizacao(s):
    lancados = []
    original = g.subprocess.Popen
    g.subprocess.Popen = lambda args, **k: lancados.append((args, k))
    g.ATUALIZACAO_AUTO = True
    g.URL_ATUALIZACAO = s.url + "/repos/x/y/releases/latest"
    try:
        g.verificar_atualizacao()
    finally:
        g.subprocess.Popen = original
        s.fechar()
    return lancados


@teste
def t_atualiza_quando_ha_versao_nova():
    lancados = rodar_atualizacao(servidor_release("v99.0.0"))
    assert len(lancados) == 1, lancados
    args, k = lancados[0]
    assert args[0].endswith("SpotifyGuardian-Setup-v99.0.0.exe") and "/VERYSILENT" in args
    with open(args[0], "rb") as f:
        assert f.read() == b"MZ instalador falso"
    os.remove(args[0])


@teste
def t_nao_atualiza_mesma_versao():
    assert rodar_atualizacao(servidor_release("v" + g.VERSAO)) == []


@teste
def t_nao_atualiza_prerelease():
    assert rodar_atualizacao(servidor_release("v99.0.0", prerelease=True)) == []


@teste
def t_nao_atualiza_sem_arquivos():
    assert rodar_atualizacao(servidor_release("v99.0.0", assets=False)) == []


@teste
def t_nao_atualiza_sha_errado():
    s = servidor_release("v99.0.1", sha="0" * 64)
    assert rodar_atualizacao(s) == []
    destino = os.path.join(tempfile.gettempdir(), "SpotifyGuardian-Setup-v99.0.1.exe")
    assert not os.path.exists(destino) and not os.path.exists(destino + ".part")


@teste
def t_nao_repete_mesmo_instalador():
    # instalador que "nao pegou" (a tag e maior que a versao dentro dele):
    # roda no maximo 1x por dia e 2x no total
    if os.path.exists(g.ESTADO_ATUALIZACAO):
        os.remove(g.ESTADO_ATUALIZACAO)
    setup = b"MZ outro instalador"
    assert len(rodar_atualizacao(servidor_release("v98.0.0", setup=setup))) == 1
    assert rodar_atualizacao(servidor_release("v98.0.0", setup=setup)) == []  # mesmo dia
    with open(g.ESTADO_ATUALIZACAO, encoding="utf-8") as f:
        estado = json.load(f)
    estado["ultima"] = time.time() - 2 * 24 * 3600
    with open(g.ESTADO_ATUALIZACAO, "w", encoding="utf-8") as f:
        json.dump(estado, f)
    assert len(rodar_atualizacao(servidor_release("v98.0.0", setup=setup))) == 1  # dia seguinte
    estado["ultima"] = time.time() - 2 * 24 * 3600
    estado["tentativas"] = 2
    with open(g.ESTADO_ATUALIZACAO, "w", encoding="utf-8") as f:
        json.dump(estado, f)
    assert rodar_atualizacao(servidor_release("v98.0.0", setup=setup)) == []  # ja tentou 2x
    # instalador novo (outro SHA) volta a valer
    assert len(rodar_atualizacao(servidor_release("v98.0.1", setup=b"MZ corrigido"))) == 1
    g._limpar_setups_antigos()


@teste
def t_atualizacao_desligada_nao_chama_rede():
    g.ATUALIZACAO_AUTO = False
    g.URL_ATUALIZACAO = "http://127.0.0.1:9/nao-deveria-chamar"
    g.verificar_atualizacao()  # nao pode levantar erro
    g.ATUALIZACAO_AUTO = True


@teste
def t_verificar_atualizacao_forcada_devolve_texto():
    # comando "verificar_atualizacao" do painel: confere mesmo com a automatica desligada
    s = servidor_release("v" + g.VERSAO)
    g.ATUALIZACAO_AUTO = False
    g.URL_ATUALIZACAO = s.url + "/repos/x/y/releases/latest"
    try:
        assert g.verificar_atualizacao() == "atualizacao automatica desligada" and s.pedidos == []
        assert g.verificar_atualizacao(forcar=True) == "ja esta na versao mais nova (%s)" % g.VERSAO
        assert len(s.pedidos) == 1
    finally:
        g.ATUALIZACAO_AUTO = True
        s.fechar()


# ============================================================ modo painel (Musica GEF)
VETORES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vetores", "uris-v1.json")
TOKEN_PC = "tokPC_" + "x" * 37                 # 43 caracteres, como o painel gera
ACESSO = "BQ-acessoSpotify_" + "y" * 60       # access token do Spotify (vem do painel)
CODIGO = "987123"
SERVIDOR_MS = 1760000000000
CONFIG_PAINEL = {"playlistUri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "playlistNome": "GEF Oficial",
                 "playlistTotal": 80, "embaralhar": True, "horarioInicio": 8, "horarioFim": 22,
                 "intervaloVerificacaoSeg": 240, "nomeDispositivo": "CAIXA-01",
                 "atualizacaoAutomatica": True, "reabrirSozinho": True, "pausadoAte": None}
HB, NOV, TOKEN, PAREAR, LOG = ("/api/guardian/heartbeat", "/api/guardian/novidades", "/api/guardian/token",
                               "/api/guardian/parear", "/api/guardian/log")
JSON = {"Content-Type": "application/json"}


class PainelFalso:
    """Painel de mentira (protocolo 1): responde pelo contrato e guarda o que recebeu.
    `forcado[rota] = (status, corpo[, cabecalhos])` simula erro numa rota."""

    def __init__(self):
        self.config = dict(CONFIG_PAINEL)
        self.versao = 7
        self.comandos = []      # entregues no proximo heartbeat (e so nele)
        self.carimbo = "c1"
        self.forcado = {}
        self.conta = "conta1"   # contaSpotifyId do PC que vai no heartbeat
        self.token_resposta = {"accessToken": ACESSO, "expiraEmSeg": 3600, "contaSpotifyId": "conta1"}
        respostas = {
            PAREAR: lambda d: {"token": TOKEN_PC, "pcId": "pc1", "pcNome": "Caixa Posto 1 - Conveniência",
                               "postoNome": "GEF 1", "config": self.config, "versaoConfig": self.versao,
                               "contaSpotifyId": "conta1", "servidorMs": SERVIDOR_MS},
            HB: self._heartbeat,
            NOV: lambda d: {"carimbo": self.carimbo, "proximaConsultaSeg": 60, "servidorMs": SERVIDOR_MS},
            TOKEN: lambda d: dict(self.token_resposta, servidorMs=SERVIDOR_MS),
            LOG: lambda d: {"ok": True},
        }
        self.s = Servidor({rota: self._rota(rota, fn) for rota, fn in respostas.items()})
        self.url = self.s.url

    def _rota(self, rota, fn):
        def responder(detalhe):
            if rota in self.forcado:
                f = self.forcado[rota]
                return f[0], f[2] if len(f) > 2 else JSON, f[1]
            return 200, JSON, fn(detalhe)
        return responder

    def _heartbeat(self, detalhe):
        comandos, self.comandos = self.comandos, []
        return {"config": self.config, "versaoConfig": self.versao, "contaSpotifyId": self.conta,
                "comandos": comandos, "carimbo": self.carimbo, "proximaConsultaSeg": 60,
                "intervaloHeartbeatSeg": 300, "servidorMs": SERVIDOR_MS}

    def pedidos(self, rota):
        return [d for d in self.s.detalhes if d["caminho"] == rota]

    def fechar(self):
        self.s.fechar()


class ModoPainel:
    """Poe o modulo no modo painel (arquivos numa pasta propria) e desfaz tudo no fim."""
    GLOBAIS = ("MODO", "IDENTIDADE", "PAINEL", "ARQ_PAINEL", "ARQ_ESTADO_PAINEL", "URL_PAINEL",
               "PLAYLIST_URI", "PLAYLIST_NOME", "PLAYLIST_TOTAL", "EMBARALHAR", "HORARIO_INICIO",
               "HORARIO_FIM", "INTERVALO", "DISPOSITIVO", "ATUALIZACAO_AUTO", "REABRIR_SOZINHO",
               "PAUSADO_ATE", "CONFIG_ERROS", "VERSAO_CONFIG_APLICADA", "RETRATO", "ULTIMO_DISPOSITIVO",
               "_ULTIMO_CICLO", "_ACORDAR", "ARQS_PREFS_SPOTIFY", "FECHAR_POPUP", "TIMEOUT",
               "reiniciar_processo", "reiniciar", "fechar_spotify", "abrir_spotify", "spotify_aberto",
               "verificar_atualizacao", "garantir_musica", "conectar", "ja_esta_rodando",
               "iniciar_vigia_de_antigos", "modo_sem_conta", "ATUALIZACAO_ATRASO")

    def __init__(self, url="http://127.0.0.1:9"):
        self.url = url

    def __enter__(self):
        self.salvos = {n: getattr(g, n) for n in self.GLOBAIS}
        self.iniciar = g.Painel.iniciar
        pasta = tempfile.mkdtemp(prefix="painel_", dir=PASTA)
        g.ARQ_PAINEL = os.path.join(pasta, "painel.json")
        g.ARQ_ESTADO_PAINEL = os.path.join(pasta, "painel_estado.json")
        g.MODO = "painel"
        g.IDENTIDADE = {"url": self.url, "pcId": "pc1", "pcNome": "Caixa 1", "token": TOKEN_PC}
        g._ACORDAR = threading.Event()
        g.ARQS_PREFS_SPOTIFY = ()
        g.FECHAR_POPUP = False
        g.PAINEL = g.Painel(g.IDENTIDADE)
        del g._REVOGACOES[:]
        g._LOG_LIMITADO.clear()
        return g.PAINEL

    def __exit__(self, *a):
        for n, v in self.salvos.items():
            setattr(g, n, v)
        g.Painel.iniciar = self.iniciar
        del g._REVOGACOES[:]
        g._LOG_LIMITADO.clear()


def pasta_guardian():
    pasta = tempfile.mkdtemp(prefix="guardian_", dir=PASTA)
    shutil.copy(ORIGEM, os.path.join(pasta, "guardian.py"))
    return pasta


def identidade_em(pasta, url):
    original = g.ARQ_PAINEL
    g.ARQ_PAINEL = os.path.join(pasta, "painel.json")
    try:
        g.gravar_identidade(url, "pc1", "Caixa 1", TOKEN_PC)
    finally:
        g.ARQ_PAINEL = original


def log_desde(tamanho):
    with open(g.LOG_FILE, "rb") as f:
        f.seek(tamanho)
        return f.read().decode("utf-8")


def entregar(pf, *comandos):
    # o painel falso entrega os comandos no proximo heartbeat
    pf.comandos = [dict(c) for c in comandos]
    g.PAINEL.heartbeat()


def resultados_entregues(pf):
    res = []
    for d in pf.pedidos(HB):
        res += d["corpo"]["resultados"]
    return res


# ------------------------------------------------------------ modo e identidade
@teste
def t_normalizar_playlist_vetores_compartilhados():
    # mesmos casos do painel (docs/vetores/uris-v1.json, copia identica nos dois repositorios)
    with open(VETORES, encoding="utf-8") as f:
        vetores = json.load(f)
    assert vetores["regexValida"] == g.PLAYLIST_VALIDA
    for caso in vetores["casos"]:
        saida = g.normalizar_playlist(caso["entrada"])
        assert saida == caso["saida"], (caso, saida)
        assert bool(re.match(vetores["regexValida"], saida)) == caso["valida"], caso


@teste
def t_decidir_modo():
    assert g.MODO == "legado" and g.IDENTIDADE is None  # este arquivo de testes roda no modo legado
    ident = {"url": "https://musica.postogef.com.br", "pcId": "pc1", "pcNome": "", "token": TOKEN_PC}
    with ModoPainel():
        assert g.decidir_modo({"client_id": "x"}, None) == "legado"
        assert g.decidir_modo(None, None) == "legado"       # config.json estragado: o erro de sempre
        assert g.decidir_modo({}, None) == "sem_conta"      # sem config.json
        assert g.decidir_modo({"startup_delay": 60}, None) == "sem_conta"
        assert g.decidir_modo({"client_id": "x"}, ident) == "painel"
        open(g.ARQ_PAINEL + ".revogado", "w").close()      # ja foi pareado: nao volta ao login antigo
        assert g.decidir_modo({"client_id": "x"}, None) == "sem_conta"
        os.remove(g.ARQ_PAINEL + ".revogado")
        open(g.ARQ_PAINEL, "w").close()                    # painel.json que nao da pra ler
        assert g.decidir_modo({"client_id": "x"}, None) == "sem_conta"


@teste
def t_identidade_dpapi_ida_e_volta():
    with ModoPainel():
        g.gravar_identidade("https://musica.postogef.com.br", "pc1", "Caixa Conveniência", TOKEN_PC)
        with open(g.ARQ_PAINEL, encoding="utf-8") as f:
            dados = json.load(f)
        assert TOKEN_PC not in json.dumps(dados) and dados["pcId"] == "pc1"  # token so protegido
        assert not os.path.exists(g.ARQ_PAINEL + ".tmp")
        assert g.ler_identidade() == {"url": "https://musica.postogef.com.br", "pcId": "pc1",
                                      "pcNome": "Caixa Conveniência", "token": TOKEN_PC}
        # estragado (ou protegido por outro usuario do Windows): ilegivel, sem erro
        bloco = base64.b64decode(dados["token"])
        dados["token"] = base64.b64encode(bloco[:-6] + b"xxxxxx").decode("ascii")
        with open(g.ARQ_PAINEL, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        assert g.ler_identidade() is None
        assert g.esquecer_identidade() is True
        assert not os.path.exists(g.ARQ_PAINEL) and os.path.exists(g.ARQ_PAINEL + ".revogado")
        assert g.ler_identidade() is None


@teste
def t_url_do_painel_so_https():
    assert g.URL_PAINEL == "https://musica.postogef.com.br"
    assert g.validar_url_painel("https://musica.postogef.com.br/") == "https://musica.postogef.com.br"
    assert g.validar_url_painel("http://127.0.0.1:8123") == "http://127.0.0.1:8123"
    assert g.validar_url_painel("http://localhost:3000") == "http://localhost:3000"
    for ruim in ("http://musica.postogef.com.br", "ftp://x.com", "", None, "musica.postogef.com.br",
                 "https://x.com/api", "https://eu:senha@x.com", "https://x.com?a=1", "https://x.com:99999",
                 "http://127.0.0.1.exemplo.com", "javascript:alert(1)"):
        try:
            g.validar_url_painel(ruim)
        except ValueError:
            continue
        raise AssertionError("aceitou %r" % (ruim,))


@teste
def t_import_tolerante_sem_rede_e_sem_thread():
    # Sem client_id o import quebrava; agora so o modo legado mostra erro de config.json.
    # Nenhum modo chama a rede nem abre thread no import.
    s = Servidor({})
    try:
        codigo = ("import sys, json, threading; sys.path.insert(0, %r); import guardian as g; "
                  "print(json.dumps([g.MODO, threading.active_count(), g.PLAYLIST_URI, g.STARTUP_DELAY, "
                  "g.URL_ATUALIZACAO == 'https://exemplo.com/x']))")
        casos = []
        p = pasta_guardian()  # pareado, com o config.json minimo e uma chave so do PC
        identidade_em(p, s.url)
        with open(os.path.join(p, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"startup_delay": 60, "url_atualizacao": "https://exemplo.com/x"}, f)
        casos.append((p, ["painel", 1, None, 60.0, True]))
        casos.append((pasta_guardian(), ["sem_conta", 1, None, 0.0, False]))  # nada: nem config.json
        p = pasta_guardian()
        with open(os.path.join(p, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"startup_delay": "lixo"}, f)  # sem client_id e com lixo: nao quebra
        casos.append((p, ["sem_conta", 1, None, 0.0, False]))
        for pasta, esperado in casos:
            r = subprocess.run([sys.executable, "-c", codigo % pasta], cwd=pasta, timeout=120,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert r.returncode == 0, (esperado, r.stdout, r.stderr)
            assert json.loads(r.stdout.decode("utf-8")) == esperado, (esperado, r.stdout)
        # modo legado com config.json estragado: o mesmo erro de sempre
        p = pasta_guardian()
        with open(os.path.join(p, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"client_id": ')
        r = subprocess.run([sys.executable, "-c", codigo % p], cwd=p, timeout=120,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert r.returncode != 0 and b"JSONDecodeError" in r.stderr
        with open(os.path.join(p, "guardian.log"), encoding="utf-8") as f:
            assert "ERRO no config.json: JSONDecodeError" in f.read()
        assert s.pedidos == []
    finally:
        s.fechar()


# ------------------------------------------------------------ pareamento
@teste
def t_parear_ok():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            config_json = os.path.join(PASTA, "config.json")
            with open(config_json, encoding="utf-8") as f:
                antes = f.read()
            resultado, mensagem, nome = g.parear_pc(" 987 123 ", pf.url)
            assert resultado == g.SAIDA_OK == 0 and nome == "Caixa Posto 1 - Conveniência", (resultado, mensagem)
            assert g.ler_identidade() == {"url": pf.url, "pcId": "pc1", "pcNome": nome, "token": TOKEN_PC}
            with open(g.ARQ_ESTADO_PAINEL, encoding="utf-8") as f:
                estado = json.load(f)
            assert estado["config"] == pf.config and estado["versaoConfig"] == 7
            with open(config_json, encoding="utf-8") as f:
                assert f.read() == antes  # config.json que ja existe (modo antigo) nao e mexido
            pedido = pf.pedidos(PAREAR)[0]
            assert pedido["corpo"]["codigo"] == CODIGO and pedido["corpo"]["versaoGuardian"] == g.VERSAO
            assert pedido["corpo"]["nomePc"] and "authorization" not in pedido["cabecalhos"]
            cortesia = pf.pedidos(HB)  # heartbeat de cortesia: o PC ja aparece online
            assert len(cortesia) == 1 and cortesia[0]["cabecalhos"]["authorization"] == "Bearer " + TOKEN_PC
            assert CODIGO not in mensagem and TOKEN_PC not in mensagem
    finally:
        pf.fechar()


@teste
def t_parear_erros():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            casos = [((404, {"erro": "x", "codigo": "codigo_invalido"}), 3),
                     ((409, {"codigo": "codigo_ambiguo"}), 3),
                     ((400, {"codigo": "formato"}), 2),
                     ((429, {"codigo": "muitas_tentativas", "tentarEmSeg": 900}), 4),
                     ((503, {"codigo": "tente_novamente"}), 4),
                     ((302, b"", {"Location": pf.url + TOKEN}), 5),   # nao segue redirecionamento
                     ((200, b"<html>portal do wi-fi</html>", {}), 5),
                     ((200, {"pcId": "pc1", "token": "curto"}), 5),
                     ((401, {"codigo": "token_revogado", "motivo": "token_revogado"}), 5)]
            for forcado, esperado in casos:
                pf.forcado[PAREAR] = forcado
                resultado, mensagem, _ = g.parear_pc(CODIGO, pf.url)
                assert resultado == esperado, (forcado, resultado, mensagem)
                assert CODIGO not in mensagem and not os.path.exists(g.ARQ_PAINEL)
            assert pf.pedidos(TOKEN) == []
            n = len(pf.s.detalhes)
            assert g.parear_pc("12345", pf.url)[0] == 2      # formato: nem chama o painel
            assert g.parear_pc("12a456", pf.url)[0] == 2
            assert g.parear_pc(None, pf.url)[0] == 2
            assert g.parear_pc(CODIGO, "http://exemplo.com")[0] == 7
            assert g.parear_pc(CODIGO, "")[0] == 7

            def nao_grava(*a):
                raise OSError(13, "Acesso negado")
            g._gravar_json_atomico, original = nao_grava, g._gravar_json_atomico
            try:
                assert g.parear_pc(CODIGO, pf.url)[0] == 6   # nao da pra gravar: nem gasta o codigo
            finally:
                g._gravar_json_atomico = original
            assert len(pf.s.detalhes) == n
            assert g.parear_pc(CODIGO, "http://127.0.0.1:9")[0] == 4   # sem rede
    finally:
        pf.fechar()


@teste
def t_parear_como_processo_igual_ao_instalador():
    # a linha de comando do instalador: python.exe guardian.py --parear CODIGO (--url so nos testes)
    pf = PainelFalso()
    try:
        pasta = pasta_guardian()
        r = subprocess.run([sys.executable, os.path.join(pasta, "guardian.py"), "--parear", CODIGO,
                            "--url", pf.url], cwd=pasta, timeout=120, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        saida = r.stdout.decode("cp1252", errors="replace")
        linhas = saida.splitlines()
        assert r.returncode == 0, (r.returncode, saida, r.stderr)
        assert "RESULTADO=0" in linhas and "PCNOME=Caixa Posto 1 - Conveniencia" in linhas, linhas
        assert CODIGO not in saida and TOKEN_PC not in saida
        with open(os.path.join(pasta, "config.json"), encoding="utf-8") as f:
            assert json.load(f) == {"startup_delay": 60}   # so criado porque nao existia
        with open(os.path.join(pasta, "guardian.log"), encoding="utf-8") as f:
            texto = f.read()
        assert "PC conectado ao painel" in texto and CODIGO not in texto and TOKEN_PC not in texto
        original = g.ARQ_PAINEL
        g.ARQ_PAINEL = os.path.join(pasta, "painel.json")
        try:
            assert g.ler_identidade()["token"] == TOKEN_PC  # DPAPI do mesmo usuario abre
        finally:
            g.ARQ_PAINEL = original
        # codigo errado: sai com 3 e nao grava nada
        pasta = pasta_guardian()
        pf.forcado[PAREAR] = (404, {"codigo": "codigo_invalido"})
        r = subprocess.run([sys.executable, os.path.join(pasta, "guardian.py"), "--parear", CODIGO,
                            "--url", pf.url], cwd=pasta, timeout=120, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert r.returncode == 3 and b"RESULTADO=3" in r.stdout and b"PCNOME=" not in r.stdout, r.stdout
        # o instalador mostra a 1a linha como o motivo: a mensagem vem antes da linha do log
        linhas = r.stdout.decode("cp1252", errors="replace").splitlines()
        assert linhas[0] == "Codigo invalido, vencido ou ja usado. Gere outro no painel.", linhas
        assert any("Pareamento com o painel nao deu certo" in x for x in linhas[2:]), linhas
        assert not os.path.exists(os.path.join(pasta, "painel.json"))
        assert not os.path.exists(os.path.join(pasta, "config.json"))
    finally:
        pf.fechar()


@teste
def t_conectar_e_desparear_pela_janela():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.IDENTIDADE = None
            g.URL_PAINEL = pf.url
            respostas = ["12", CODIGO]   # 1o com formato errado, depois o certo
            reinicios = []
            g.input = lambda texto="": respostas.pop(0)
            g.reiniciar = lambda: reinicios.append(1)
            try:
                g.conectar_painel()
                assert reinicios == [1] and g.ler_identidade()["pcId"] == "pc1" and respostas == []
                respostas[:] = ["n", ""]
                g.desparear()   # nao confirmou
                assert os.path.exists(g.ARQ_PAINEL) and reinicios == [1]
                respostas[:] = ["s"]
                g.desparear()
                assert not os.path.exists(g.ARQ_PAINEL) and os.path.exists(g.ARQ_PAINEL + ".revogado")
                assert reinicios == [1, 1]
            finally:
                del g.input
    finally:
        pf.fechar()


@teste
def t_relogin_no_modo_painel_so_orienta():
    pasta = pasta_guardian()
    identidade_em(pasta, "https://musica.postogef.com.br")
    r = subprocess.run([sys.executable, os.path.join(pasta, "guardian.py"), "--relogin"], cwd=pasta,
                       timeout=120, input=b"\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0 and b"Reconecte a conta no painel" in r.stdout, (r.stdout, r.stderr)
    assert not os.path.exists(os.path.join(pasta, ".cache_gef"))


# ------------------------------------------------------------ token do Spotify vindo do painel
@teste
def t_spotipy_chama_o_token_do_painel():
    pf = PainelFalso()
    spotify = Servidor({"/v1/me/player": (200, JSON, {"is_playing": True, "device": PC})})
    try:
        with ModoPainel(pf.url):
            sp = g.conectar()
            assert isinstance(sp.auth_manager, g.TokenDoPainel)  # nada de SpotifyOAuth/navegador
            sp.prefix = spotify.url + "/v1/"
            assert sp.current_playback()["is_playing"] is True
            assert spotify.detalhes[0]["cabecalhos"]["authorization"] == "Bearer " + ACESSO
            pedidos = pf.pedidos(TOKEN)
            assert len(pedidos) == 1 and pedidos[0]["corpo"] == {"forcar": False}
            assert pedidos[0]["cabecalhos"]["authorization"] == "Bearer " + TOKEN_PC
    finally:
        pf.fechar()
        spotify.fechar()


@teste
def t_token_cache_e_renovacao():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            tk = g.TokenDoPainel(g.PAINEL)
            assert tk.get_access_token(as_dict=False) == ACESSO and tk.get_access_token() == ACESSO
            assert len(pf.pedidos(TOKEN)) == 1                       # cache em memoria
            # renova 10 min antes de vencer (+0.001: no Python 3.8 o relogio anda de 15,6 em 15,6 ms
            # e (t + 3000) - t pode dar 3000.0000000000005 se ele nao andou)
            assert 2900 < tk._renovar_em - time.monotonic() <= 3000.001
            assert g.PAINEL.conta_em_uso == "conta1"
            tk._renovar_em = time.monotonic() - 1                    # chegou a hora
            pf.token_resposta = dict(pf.token_resposta, accessToken="BQ-novo")
            assert tk.get_access_token() == "BQ-novo" and len(pf.pedidos(TOKEN)) == 2
            pf.token_resposta = {"accessToken": "", "expiraEmSeg": 3600}   # resposta fora do contrato
            tk._renovar_em = tk._vence_em = 0
            try:
                tk.get_access_token()
                raise AssertionError("aceitou token vazio")
            except g.RespostaInvalida:
                pass
    finally:
        pf.fechar()


@teste
def t_token_ignora_o_relogio_do_pc():
    pf = PainelFalso()
    original = g.time.time
    try:
        with ModoPainel(pf.url):
            g.time.time = lambda: 4102444800.0  # PC acha que esta em 2100 (o servidor diz 2025)
            tk = g.TokenDoPainel(g.PAINEL)
            tk.get_access_token()
            tk.get_access_token()
            assert len(pf.pedidos(TOKEN)) == 1
    finally:
        g.time.time = original
        pf.fechar()


@teste
def t_token_painel_fora_usa_o_que_tem():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            tk = g.TokenDoPainel(g.PAINEL)
            tk.get_access_token()
            pf.forcado[TOKEN] = (503, {"codigo": "tente_novamente", "tentarEmSeg": 30})
            tk._renovar_em = time.monotonic() - 1   # hora de renovar, mas o painel caiu
            assert tk.get_access_token() == ACESSO
            tk._vence_em = time.monotonic() + 30    # falta menos de 1 min para vencer: nao usa mais
            for gerente in (tk, g.TokenDoPainel(g.PAINEL)):   # e sem token nenhum
                try:
                    gerente.get_access_token()
                    raise AssertionError("deveria dar PainelIndisponivel")
                except g.PainelIndisponivel as e:
                    assert isinstance(e, requests.exceptions.RequestException) and e.status == 503
    finally:
        pf.fechar()


@teste
def t_token_409_conta_pendente():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            pf.forcado[TOKEN] = (409, {"erro": "x", "codigo": "reautorizar", "motivo": "reautorizar"})
            tk = g.TokenDoPainel(g.PAINEL)
            try:
                tk.get_access_token()
                raise AssertionError("deveria dar ContaPendente")
            except g.ContaPendente as e:
                assert e.motivo == "reautorizar"
            assert g.PAINEL.montar_heartbeat()["contaSpotify"] == {"ok": False, "motivo": "reautorizar"}
            del pf.forcado[TOKEN]
            assert tk.get_access_token() == ACESSO
            assert g.PAINEL.montar_heartbeat()["contaSpotify"] == {"ok": True}
    finally:
        pf.fechar()


@teste
def t_token_depois_de_401_do_spotify_pede_com_forcar():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            tk = g.TokenDoPainel(g.PAINEL)
            tk.get_access_token()
            tk.esquecer()
            tk.get_access_token()
            tk._renovar_em = time.monotonic() - 1
            tk.get_access_token()
            assert [d["corpo"] for d in pf.pedidos(TOKEN)] == [{"forcar": False}, {"forcar": True},
                                                              {"forcar": False}]
    finally:
        pf.fechar()


@teste
def t_token_conta_trocada_no_painel_nao_usa_o_cache():
    # trocar ou tirar a conta do PC no painel invalida o access token em cache
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            tk = g.TokenDoPainel(g.PAINEL)
            assert tk.get_access_token() == ACESSO and g.PAINEL.conta_em_uso == "conta1"
            g.PAINEL.heartbeat()                     # mesma conta: segue com o cache
            assert tk.get_access_token() == ACESSO and len(pf.pedidos(TOKEN)) == 1
            pf.conta = "conta2"                      # conta trocada no painel
            pf.token_resposta = {"accessToken": "BQ-conta2", "expiraEmSeg": 3600, "contaSpotifyId": "conta2"}
            g.PAINEL.heartbeat()
            assert tk.get_access_token() == "BQ-conta2" and len(pf.pedidos(TOKEN)) == 2
            assert g.PAINEL.conta_em_uso == "conta2" and g.PAINEL.conta_trocou is False
            g.PAINEL.heartbeat()                     # ja e a conta nova: cache de novo
            assert tk.get_access_token() == "BQ-conta2" and len(pf.pedidos(TOKEN)) == 2
            pf.conta = None                          # conta tirada do PC
            pf.forcado[TOKEN] = (409, {"codigo": "sem_conta", "motivo": "sem_conta"})
            g.PAINEL.heartbeat()
            try:
                tk.get_access_token()
                raise AssertionError("deveria dar ContaPendente")
            except g.ContaPendente as e:
                assert e.motivo == "sem_conta"
            assert [d["corpo"] for d in pf.pedidos(TOKEN)] == [{"forcar": False}] * 3
    finally:
        pf.fechar()


# ------------------------------------------------------------ config do painel
@teste
def t_config_do_painel_aplicada_e_validada():
    with ModoPainel():
        locais = (g.URL_ATUALIZACAO, g.STARTUP_DELAY, g.FECHAR_POPUP, g.ATUALIZACAO_ATRASO)
        invasao = dict(CONFIG_PAINEL, url_atualizacao="https://exemplo.com/mal", startup_delay=0,
                       fechar_popup=True, atualizacao_atraso_s=1, client_id="x")
        linha = g.aplicar_config_painel(invasao, 7, SERVIDOR_MS)
        assert g.PLAYLIST_URI == CONFIG_PAINEL["playlistUri"] and g.PLAYLIST_NOME == "GEF Oficial"
        assert (g.PLAYLIST_TOTAL, g.HORARIO_INICIO, g.HORARIO_FIM, g.INTERVALO, g.DISPOSITIVO) == (80, 8, 22, 240, "CAIXA-01")
        assert g.CONFIG_ERROS == [] and g.VERSAO_CONFIG_APLICADA == 7 and "versao 7" in linha
        assert (g.URL_ATUALIZACAO, g.STARTUP_DELAY, g.FECHAR_POPUP, g.ATUALIZACAO_ATRASO) == locais  # so do PC
        assert g.aplicar_config_painel(dict(CONFIG_PAINEL), 7, SERVIDOR_MS) is None  # nada mudou: nada no log
        # valores errados: fica o anterior e o erro vai para o painel
        ruim = dict(CONFIG_PAINEL, horarioInicio=25, intervaloVerificacaoSeg=10, embaralhar="sim",
                    playlistUri="spotify:album:1ATL5GLyefJaxhQzSPVrLX", playlistTotal=-1, nomeDispositivo=5)
        linha = g.aplicar_config_painel(ruim, 8, SERVIDOR_MS)
        assert (g.HORARIO_INICIO, g.INTERVALO, g.EMBARALHAR, g.DISPOSITIVO) == (8, 240, True, "CAIXA-01")
        assert g.PLAYLIST_URI == CONFIG_PAINEL["playlistUri"] and g.PLAYLIST_TOTAL == 80
        assert "horarioInicio invalido: 25" in g.CONFIG_ERROS and len(g.CONFIG_ERROS) == 6, g.CONFIG_ERROS
        assert "ERROS" in linha and g.PAINEL.montar_heartbeat()["configErros"] == g.CONFIG_ERROS
        # playlist nova (link copiado do app) zera o tamanho guardado
        g.aplicar_config_painel(dict(CONFIG_PAINEL, playlistTotal=None,
                                     playlistUri="https://open.spotify.com/playlist/4p3LjEkrRzUVQeCOmiLDSt?si=x"), 9, SERVIDOR_MS)
        assert g.PLAYLIST_URI == "spotify:playlist:4p3LjEkrRzUVQeCOmiLDSt" and g.PLAYLIST_TOTAL is None
        # com o playlistTotal do painel o sorteio nao le a playlist (que pode nao ser da conta da loja)
        g.aplicar_config_painel(dict(CONFIG_PAINEL, playlistTotal=30), 10, SERVIDOR_MS)
        sp = SpotifyFalso()
        offset = g.calcular_offset(sp)
        assert sp.nomes() == [] and 0 <= offset["position"] < 30
        # chave que falta volta ao padrao do contrato
        g.aplicar_config_painel({}, 11, SERVIDOR_MS)
        assert (g.PLAYLIST_URI, g.HORARIO_INICIO, g.INTERVALO, g.EMBARALHAR, g.DISPOSITIVO) == (None, None, 180, True, "")


@teste
def t_config_do_painel_em_cache_no_disco():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.PAINEL.heartbeat()
            with open(g.ARQ_ESTADO_PAINEL, encoding="utf-8") as f:
                assert json.load(f)["config"] == pf.config
            config, versao, servidor_ms, _ = g.PAINEL.pegar_config_nova()
            assert (config, versao, servidor_ms) == (pf.config, 7, SERVIDOR_MS)
            assert g.PAINEL.pegar_config_nova() is None
            pf.fechar()   # guardian reiniciado sem internet: a config vem do disco
            assert g.Painel(g.IDENTIDADE).config_em_cache() == (pf.config, 7)
    finally:
        pf.fechar()


@teste
def t_sem_playlist_nao_toca_nada():
    with ModoPainel():
        reset()
        g.aplicar_config_painel(dict(CONFIG_PAINEL, playlistUri=None, horarioInicio=None, horarioFim=None),
                                7, SERVIDOR_MS)
        conferidos = []
        g.spotify_aberto = lambda: conferidos.append(1) or True
        sp = SpotifyFalso(current_playback={"is_playing": False}, devices={"devices": [PC]})
        g.garantir_musica(sp)
        assert sp.nomes() == [] and conferidos == [1]   # so mantem o Spotify aberto


@teste
def t_pausado_pelo_painel_nao_retoma():
    with ModoPainel():
        reset()
        config = dict(CONFIG_PAINEL, nomeDispositivo="", horarioInicio=None, horarioFim=None)
        agora = time.monotonic()
        g.aplicar_config_painel(dict(config, pausadoAte=SERVIDOR_MS + 600000), 7, SERVIDOR_MS, agora)
        assert g.pausado() and abs(g.PAUSADO_ATE - agora - 600) < 0.001   # pelo relogio do servidor
        sp = SpotifyFalso(current_playback={"is_playing": False}, devices={"devices": [PC]})
        g.garantir_musica(sp)
        assert sp.nomes() == []
        g.aplicar_config_painel(dict(config, pausadoAte=SERVIDOR_MS - 1000), 7, SERVIDOR_MS)
        assert not g.pausado()
        g.garantir_musica(sp)
        assert "start_playback" in sp.nomes()
        g.aplicar_config_painel(dict(config, pausadoAte=SERVIDOR_MS + 600000), 7)
        assert not g.pausado()   # config do cache (sem servidorMs): nao da pra saber quanto falta


# ------------------------------------------------------------ heartbeat e novidades
@teste
def t_heartbeat_conteudo():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.ERROS_RECENTES.clear()
            prefs = os.path.join(PASTA, "prefs_spotify")
            with open(prefs, "w", encoding="utf-8") as f:
                f.write('app.autostart-mode="minimized"\nautologin.username="outro"\n'
                        'autologin.canonical_username="gef1loja"\n')
            g.ARQS_PREFS_SPOTIFY = (os.path.join(PASTA, "nao_existe"), prefs)
            g.aplicar_config_painel(dict(CONFIG_PAINEL), 7, SERVIDOR_MS)
            pb = {"is_playing": True, "progress_ms": 5000, "context": {"uri": "spotify:playlist:X"},
                  "device": dict(PC, volume_percent=80),
                  "item": {"name": "Faixa", "uri": "spotify:track:1", "duration_ms": 200000,
                           "artists": [{"name": "A"}, {"name": "B"}],
                           "album": {"name": "Disco", "images": [{"url": "https://i.scdn.co/image/1"}]}}}
            g.registrar_reproducao(pb)
            g.registrar_reproducao(dict(pb, device=CEL))   # celular nunca vira o aparelho do PC
            g.ciclo_concluido()
            g.log("Spotify instavel (teste)", erro=True)
            g.log("so informacao")
            g.PAINEL.resultado("cmd-1", True, "ok")
            g.PAINEL.heartbeat()
            d = pf.pedidos(HB)[0]
            c = d["corpo"]
            assert d["metodo"] == "POST" and d["cabecalhos"]["authorization"] == "Bearer " + TOKEN_PC
            assert d["cabecalhos"]["user-agent"] == "SpotifyGuardian/" + g.VERSAO
            assert (c["protocolo"], c["versaoGuardian"], c["modo"], c["versaoConfigAplicada"]) == (1, g.VERSAO, "painel", 7)
            assert c["sistema"]["python"] == sys.version.split()[0] and c["sistema"]["computador"]
            assert isinstance(c["sistema"]["relogioPcMs"], int) and isinstance(c["sistema"]["ligadoHaMin"], int)
            assert c["spotify"] == {"aberto": True, "usuarioLogado": "gef1loja"}
            r = c["reproducao"]
            assert (r["tocando"], r["faixa"], r["artistas"], r["album"]) == (True, "Faixa", ["A", "B"], "Disco")
            assert r["capaUrl"] == "https://i.scdn.co/image/1" and r["contextoUri"] == "spotify:playlist:X"
            assert (r["faixaUri"], r["duracaoMs"], r["progressoMs"]) == ("spotify:track:1", 200000, 5000)
            assert 0 <= r["idadeSeg"] < 5
            assert c["dispositivo"] == {"id": "pc-loja", "nome": "LOJA-PC", "tipo": "Computer",
                                        "volume": 80, "ativo": False}
            assert isinstance(c["dentroDoHorario"], bool) and c["pausado"] is False
            assert c["ultimoCicloSeg"] == 0 and c["contaSpotify"] == {"ok": True}
            assert [e["mensagem"] for e in c["erros"]] == ["Spotify instavel (teste)"]
            assert c["configErros"] == [] and c["resultados"] == [{"comandoId": "cmd-1", "ok": True, "mensagem": "ok"}]
            assert TOKEN_PC not in json.dumps(c)
            g.PAINEL.heartbeat()
            assert pf.pedidos(HB)[1]["corpo"]["resultados"] == []   # resultado entregue nao vai de novo
    finally:
        pf.fechar()


@teste
def t_erros_recentes_no_heartbeat():
    with ModoPainel():
        g.ERROS_RECENTES.clear()
        for i in range(25):
            g.log("erro %d %s" % (i, "ab " * 150), erro=True)
        erros = g.PAINEL.montar_heartbeat()["erros"]
        assert len(erros) == 20 and erros[0]["mensagem"].startswith("erro 5 ")
        assert erros[-1]["mensagem"].startswith("erro 24 ")
        assert all(len(e["mensagem"]) <= 300 and 0 <= e["haSeg"] < 5 and e["hora"] for e in erros)
        g.ERROS_RECENTES.clear()


@teste
def t_log_com_duas_threads():
    tamanho = os.path.getsize(g.LOG_FILE)

    def escrever(nome):
        for i in range(200):
            g.log("%s linha %03d fim" % (nome, i))
    threads = [threading.Thread(target=escrever, args=(n,)) for n in ("thread-A", "thread-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    linhas = [l for l in log_desde(tamanho).splitlines() if " linha " in l]
    assert len(linhas) == 400 and all(re.match(r"^\[[^\]]+\] thread-[AB] linha \d{3} fim$", l) for l in linhas)


@teste
def t_novidades_espera_crescente_e_carimbo():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            p = g.PAINEL
            assert 55 < p.passo() <= 60 and len(pf.pedidos(HB)) == 1   # 1o heartbeat na hora
            p.proxima_nov = 0
            p.passo()   # carimbo igual: so a consulta (1 leitura no painel)
            assert len(pf.pedidos(NOV)) == 1 and len(pf.pedidos(HB)) == 1
            pf.carimbo = "c2"
            p.proxima_nov = 0
            p.passo()   # carimbo novo: heartbeat na hora
            assert len(pf.pedidos(HB)) == 2
            tamanho = os.path.getsize(g.LOG_FILE)
            pf.forcado[NOV] = (503, {"codigo": "tente_novamente"})
            esperas = []
            for _ in range(6):
                p.proxima_nov = 0
                esperas.append(round(p.passo()))
            assert esperas == [60, 120, 240, 480, 900, 900], esperas
            assert log_desde(tamanho).count("Painel indisponivel") == 1   # log limitado
            del pf.forcado[NOV]
            p.proxima_nov = 0
            p.passo()
            assert p.falhas == 0 and "O painel voltou a responder" in log_desde(tamanho)
            pf.forcado[NOV] = (404, {})   # painel sem a rota: fica so com o heartbeat
            p.proxima_nov = 0
            p.passo()
            n = len(pf.pedidos(NOV))
            p.proxima_nov = 0
            assert p.passo() > 200 and len(pf.pedidos(NOV)) == n and p.com_novidades is False
    finally:
        pf.fechar()


@teste
def t_painel_com_timeout_e_sem_redirecionamento():
    pf = PainelFalso()
    lento = Servidor({NOV: lambda d: (threading.Event().wait(3), (200, JSON, {}))[1]})
    try:
        with ModoPainel(pf.url):
            chamadas = []
            original = g.requests.request

            def espiao(*a, **k):
                chamadas.append(k)
                return original(*a, **k)
            g.requests.request = espiao
            try:
                g.PAINEL.heartbeat()
                g.PAINEL.novidades()
                g.PAINEL.token(False)
                g.PAINEL.enviar_log(None, 5)
            finally:
                g.requests.request = original
            assert len(chamadas) == 4
            assert all(k["timeout"] == g.TIMEOUT and k["allow_redirects"] is False for k in chamadas)
            pf.forcado[HB] = (302, b"", {"Location": pf.url + TOKEN})
            n = len(pf.pedidos(TOKEN))
            try:
                g.PAINEL.heartbeat()
                raise AssertionError("seguiu o redirecionamento")
            except g.RespostaInvalida:
                pass
            assert len(pf.pedidos(TOKEN)) == n
            g.TIMEOUT = (2, 0.5)   # painel que nao responde: desiste no prazo
            p = g.Painel(dict(g.IDENTIDADE, url=lento.url))
            inicio = time.monotonic()
            try:
                p.novidades()
                raise AssertionError("deveria desistir")
            except g.PainelIndisponivel:
                pass
            assert time.monotonic() - inicio < 2.5
    finally:
        pf.fechar()
        lento.fechar()


# ------------------------------------------------------------ comandos
@teste
def t_comando_executa_uma_vez_mesmo_depois_de_reiniciar():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            reset()
            feitos = []
            g.fechar_spotify = lambda: feitos.append("fechar")
            g.abrir_spotify = lambda: feitos.append("abrir") or True
            cmd = {"id": "cmd-1", "tipo": "reabrir_spotify", "criadoEm": SERVIDOR_MS - 5000}
            entregar(pf, cmd)
            assert g._ACORDAR.is_set()   # o laco principal acorda
            g.na_thread_principal(None)
            assert feitos == ["fechar", "abrir"] and not g._ACORDAR.is_set()
            g.PAINEL = g.Painel(g.IDENTIDADE)   # guardian reiniciado: le o estado do disco
            entregar(pf, cmd)                   # o painel entrega de novo
            g.na_thread_principal(None)
            assert feitos == ["fechar", "abrir"]   # nao repetiu
            assert resultados_entregues(pf) == [{"comandoId": "cmd-1", "ok": True, "mensagem": "Spotify reaberto"}]
    finally:
        pf.fechar()


@teste
def t_comando_desconhecido_e_vencido():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.reiniciar_processo = perigo("reiniciar_processo")
            entregar(pf, {"id": "c-x", "tipo": "formatar_disco", "criadoEm": SERVIDOR_MS},
                     {"id": "c-velho", "tipo": "reiniciar_guardian", "criadoEm": SERVIDOR_MS - 31 * 60000})
            g.na_thread_principal(None)
            # chegou no prazo, mas o laco principal so pegou depois de 30 min (ex.: espera de um 429)
            entregar(pf, {"id": "c-tarde", "tipo": "reiniciar_guardian", "criadoEm": SERVIDOR_MS - 29 * 60000})
            g.PAINEL.estado["comandosPendentes"][0]["recebido"] -= 120
            g.na_thread_principal(None)
            g.PAINEL.heartbeat()
            res = resultados_entregues(pf)
            assert {"comandoId": "c-x", "ok": False, "mensagem": "comando desconhecido"} in res
            assert {"comandoId": "c-velho", "ok": False, "mensagem": "comando expirado"} in res
            assert {"comandoId": "c-tarde", "ok": False, "mensagem": "comando expirado"} in res
    finally:
        pf.fechar()


@teste
def t_comando_reiniciar_guardian():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            chamadas = []

            def reiniciar_falso(motivo):
                with open(g.ARQ_ESTADO_PAINEL, encoding="utf-8") as f:
                    chamadas.append((motivo, json.load(f)))   # o que esta em disco na hora da troca
            g.reiniciar_processo = reiniciar_falso
            entregar(pf, {"id": "c-r", "tipo": "reiniciar_guardian", "criadoEm": SERVIDOR_MS})
            g.na_thread_principal(None)
            motivo, estado = chamadas[0]
            assert motivo == "comando do painel" and len(chamadas) == 1
            assert estado["resultadosPendentes"] == [{"comandoId": "c-r", "ok": True, "mensagem": "guardian reiniciado"}]
            assert estado["comandosPendentes"] == [] and "c-r" in estado["comandosFeitos"]
    finally:
        pf.fechar()


@teste
def t_reiniciar_processo_segura_a_tarefa_e_troca_o_processo():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    nome = "Local\\SpotifyGuardianTesteReinicio-%d" % os.getpid()
    originais = (g.MUTEX_NOME, g._MUTEX, g.iniciar_em_segundo_plano, g.os._exit)
    eventos = []
    novos = []

    def abrir_novo():
        eventos.append(("novo", g._mutex_existe(g.MUTEX_AJUDANTE), g._mutex_existe(nome)))
        novos.append(k32.CreateMutexW(None, False, nome))   # o guardian novo abre

    def sair(codigo):
        eventos.append(("saiu", codigo))
        raise Pare()
    g.MUTEX_NOME, g._MUTEX = nome, None
    g.time.sleep = RelogioFalso()
    try:
        assert g.ja_esta_rodando() is False and g._mutex_existe(nome)   # este "guardian" esta no ar
        g.iniciar_em_segundo_plano, g.os._exit = abrir_novo, sair
        try:
            g.reiniciar_processo("teste")
        except Pare:
            pass
    finally:
        g.MUTEX_NOME, g._MUTEX, g.iniciar_em_segundo_plano, g.os._exit = originais
        g._soltar_tarefa_vigia()
        for h in novos:
            k32.CloseHandle(h)
    # a tarefa agendada ficou segura e o mutex antigo ja estava solto quando o novo abriu
    assert eventos == [("novo", True, False), ("saiu", 0)], eventos


@teste
def t_comando_verificar_atualizacao():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            chamadas = []
            g.verificar_atualizacao = lambda forcar=False: chamadas.append(forcar) or "ja esta na versao mais nova"
            entregar(pf, {"id": "c-a", "tipo": "verificar_atualizacao", "criadoEm": SERVIDOR_MS})
            g.na_thread_principal(None)
            assert chamadas == [True]
            g.PAINEL.heartbeat()
            assert {"comandoId": "c-a", "ok": True, "mensagem": "ja esta na versao mais nova"} in resultados_entregues(pf)
    finally:
        pf.fechar()


@teste
def t_comando_retomar_musica_e_erro_vira_resultado():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            def falha(sp):
                raise SE(503, -1, "instavel")
            g.garantir_musica = falha
            entregar(pf, {"id": "c-m", "tipo": "retomar_musica", "criadoEm": SERVIDOR_MS})
            g.na_thread_principal(object())
            g.PAINEL.heartbeat()
            res = [r for r in resultados_entregues(pf) if r["comandoId"] == "c-m"]
            assert len(res) == 1 and res[0]["ok"] is False and "HTTP 503" in res[0]["mensagem"]
    finally:
        pf.fechar()


@teste
def t_comando_enviar_log_sem_segredos():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.PAINEL.heartbeat()
            # algo parecido com segredo foi parar no log (ex.: erro de outra biblioteca)
            g.log("Authorization: Bearer %s" % TOKEN_PC)
            g.log("callback?code=AQBxyz123&state=1 token=%s" % ACESSO)
            g.log("client_secret: 0123456789abcdef0123456789abcdef")
            g.log("linha final do teste")
            entregar(pf, {"id": "c-log", "tipo": "enviar_log", "criadoEm": SERVIDOR_MS, "parametros": {"linhas": 4}})
            enviado = pf.pedidos(LOG)[0]["corpo"]
            assert enviado["comandoId"] == "c-log" and enviado["texto"].count("\n") == 3, enviado
            assert enviado["texto"].endswith("linha final do teste"), enviado
            for segredo in (TOKEN_PC, ACESSO, "AQBxyz123", "0123456789abcdef0123456789abcdef"):
                assert segredo not in enviado["texto"], segredo
            assert pf.pedidos(LOG)[0]["cabecalhos"]["authorization"] == "Bearer " + TOKEN_PC
            g.PAINEL.passo()   # resultado sai no heartbeat seguinte
            g.PAINEL.proximo_hb = 0
            g.PAINEL.passo()
            assert {"comandoId": "c-log", "ok": True, "mensagem": "log enviado (ultimas 4 linhas)"} in resultados_entregues(pf)
            # log grande: so o fim, ate 64 KB
            for i in range(1000):
                g.log("linha comprida %04d %s" % (i, "ab " * 40))
            g.PAINEL.enviar_log("c-grande", 1000)
            texto = pf.pedidos(LOG)[-1]["corpo"]["texto"]
            assert len(texto.encode("utf-8")) <= 64 * 1024, len(texto)
            assert texto.endswith("linha comprida 0999 " + "ab " * 40), texto[-300:]
    finally:
        pf.fechar()


@teste
def t_comando_sincronizar_manda_heartbeat_logo():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            p = g.PAINEL
            p.passo()
            pf.comandos = [{"id": "c-s", "tipo": "sincronizar", "criadoEm": SERVIDOR_MS}]
            pf.carimbo = "c9"
            p.proxima_nov = 0
            p.passo()   # novidades -> carimbo novo -> heartbeat (traz o sincronizar)
            assert p._pedido.is_set()   # a thread manda outro heartbeat ja, com o resultado
            p._pedido.clear()
            p.proximo_hb = 0
            p.passo()
            assert {"comandoId": "c-s", "ok": True, "mensagem": "heartbeat enviado"} in pf.pedidos(HB)[-1]["corpo"]["resultados"]
    finally:
        pf.fechar()


# ------------------------------------------------------------ revogacao
@teste
def t_revogacao_nao_confirmada():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.gravar_identidade(pf.url, "pc1", "Caixa 1", TOKEN_PC)
            g.reiniciar_processo = perigo("reiniciar_processo")
            p = g.PAINEL
            p.passo()
            pf.forcado[NOV] = (401, b"<html>Vercel Authentication</html>", {})   # 401 qualquer: nao conta
            p.proxima_nov = 0
            p.passo()
            assert g._REVOGACOES == [] and p.falhas == 1
            pf.forcado[HB] = (401, {"codigo": "token_revogado", "motivo": "token_revogado"})
            for _ in range(3):   # 3 respostas, mas em menos de 10 min: ainda nao
                p.proximo_hb = 0
                p.passo()
            assert len(g._REVOGACOES) == 3 and os.path.exists(g.ARQ_PAINEL)
            del pf.forcado[HB]
            del pf.forcado[NOV]
            p.proximo_hb = 0
            p.passo()   # o painel voltou a aceitar o token: zera a contagem
            assert g._REVOGACOES == [] and os.path.exists(g.ARQ_PAINEL)
    finally:
        pf.fechar()


@teste
def t_revogacao_confirmada():
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            g.gravar_identidade(pf.url, "pc1", "Caixa 1", TOKEN_PC)
            chamadas = []
            g.reiniciar_processo = lambda motivo: chamadas.append(motivo)
            pf.forcado[TOKEN] = (401, {"codigo": "token_revogado", "motivo": "token_revogado"})
            tk = g.TokenDoPainel(g.PAINEL)
            for i in range(3):
                try:
                    tk.get_access_token()
                    raise AssertionError("deveria dar TokenRevogado")
                except g.TokenRevogado:
                    g.tratar_revogacao()
                if i == 1:
                    assert chamadas == [] and os.path.exists(g.ARQ_PAINEL)
                    g._REVOGACOES[0] -= 601   # a 1a resposta foi ha mais de 10 min
            assert chamadas == ["PC desconectado do painel"]
            assert not os.path.exists(g.ARQ_PAINEL) and os.path.exists(g.ARQ_PAINEL + ".revogado")
            # reiniciado, nao volta para o login antigo do config.json: espera pareamento novo
            assert g.decidir_modo({"client_id": "x"}, g.ler_identidade()) == "sem_conta"
    finally:
        pf.fechar()


@teste
def t_revogacao_confirmada_com_pareamento_novo_no_disco():
    # o PC foi pareado de novo com este processo ainda rodando: o painel.json novo fica
    pf = PainelFalso()
    try:
        with ModoPainel(pf.url):
            novo = "tokNovo_" + "z" * 35
            g.gravar_identidade(pf.url, "pc1", "Caixa 1", novo)
            chamadas = []
            g.reiniciar_processo = lambda motivo: chamadas.append(motivo)
            for _ in range(3):
                g.tratar_revogacao()
                g._REVOGACOES[0] -= 601   # a 1a resposta foi ha mais de 10 min
            assert chamadas == ["pareamento novo no painel.json"]
            assert not os.path.exists(g.ARQ_PAINEL + ".revogado")
            assert g.ler_identidade()["token"] == novo
    finally:
        pf.fechar()


# ------------------------------------------------------------ laco principal (modo painel)
class AcordarFalso:
    """Faz o papel do _ACORDAR no laco principal: anota a espera e para o laco."""

    def __init__(self, relogio):
        self.relogio = relogio

    def wait(self, s):
        self.relogio(("acordar", s))
        return False

    def set(self):
        pass

    def clear(self):
        pass


def rodar_main_painel(erro, n=1, config=CONFIG_PAINEL, conectar=None):
    reset()
    relogio = RelogioFalso(parar_depois=n)
    g.time.sleep = relogio
    g.ATUALIZACAO_ATRASO = 10 ** 9
    if config is not None:
        g._gravar_json_atomico(g.ARQ_ESTADO_PAINEL, {"config": config, "versaoConfig": 7})
    if erro is not None:
        def falha(sp):
            raise erro
        g.garantir_musica = falha
    g.conectar = conectar or (lambda: object())
    g.ja_esta_rodando = lambda: False
    g.iniciar_vigia_de_antigos = lambda: None
    g.Painel.iniciar = lambda self: None   # a thread do painel nao sobe no teste
    g._ACORDAR = AcordarFalso(relogio)
    try:
        g.main()
    except Pare:
        pass
    return relogio.esperas


@teste
def t_main_painel_conta_pendente_espera_600():
    with ModoPainel():
        tamanho = os.path.getsize(g.LOG_FILE)
        esperas = rodar_main_painel(g.ContaPendente("reautorizar"), n=3)
        assert esperas == [("acordar", 600)] * 3, esperas
        novo = log_desde(tamanho)
        assert novo.count("precisa de atencao no painel (reautorizar)") == 1   # log limitado
        assert "Modo painel" in novo and "Configuracao do painel (versao 7)" in novo
        assert g.PLAYLIST_URI == CONFIG_PAINEL["playlistUri"]   # config do cache aplicada no inicio


@teste
def t_main_painel_fora_do_ar_tenta_logo():
    with ModoPainel():
        esperas = rodar_main_painel(g.PainelIndisponivel("/api/guardian/token: HTTP 503"), n=4)
        assert esperas == [("acordar", 20)] * 3 + [("acordar", 240)], esperas   # 240 = intervalo do painel


@teste
def t_main_painel_429_nunca_e_encurtado():
    with ModoPainel():
        assert rodar_main_painel(SE(429, -1, "x", headers={"Retry-After": "120"})) == [120]   # time.sleep


@teste
def t_main_painel_token_revogado():
    with ModoPainel():
        chamadas = []
        g.reiniciar_processo = lambda motivo: chamadas.append(motivo)
        esperas = rodar_main_painel(g.TokenRevogado(), n=2)
        assert esperas == [("acordar", 240)] * 2 and len(g._REVOGACOES) == 2 and chamadas == []


@teste
def t_main_painel_401_do_spotify_pede_outro_token():
    pf = PainelFalso()
    spotify = Servidor({"/v1/me/player": (401, JSON, {"error": {"status": 401, "message": "The access token expired"}})})
    try:
        with ModoPainel(pf.url):
            conectar = g.conectar

            def conectar_teste():
                sp = conectar()
                sp.prefix = spotify.url + "/v1/"
                return sp
            config = dict(CONFIG_PAINEL, horarioInicio=None, horarioFim=None)
            esperas = rodar_main_painel(None, n=2, config=config, conectar=conectar_teste)
            assert esperas == [("acordar", 20), ("acordar", 20)], esperas
            assert [d["corpo"] for d in pf.pedidos(TOKEN)] == [{"forcar": False}, {"forcar": True}]
    finally:
        pf.fechar()
        spotify.fechar()


@teste
def t_esperar_proximo_ciclo_acorda_com_comando():
    with ModoPainel():
        g.PAINEL = g.Painel(g.IDENTIDADE)
        relogio = RelogioFalso()
        g.time.sleep = relogio
        g._ACORDAR.set()
        inicio = time.monotonic()
        g.esperar_proximo_ciclo(30)   # comando chegou: nao espera
        assert time.monotonic() - inicio < 1 and relogio.esperas == []
        g.esperar_proximo_ciclo(30, limite_do_spotify=True)   # 429: espera inteira mesmo assim
        assert relogio.esperas == [30]
    relogio = RelogioFalso()   # modo legado: exatamente o time.sleep
    g.time.sleep = relogio
    g.esperar_proximo_ciclo(180)
    assert relogio.esperas == [180]


@teste
def t_modo_legado_nao_fala_com_o_painel():
    from spotipy.oauth2 import SpotifyOAuth
    assert g.MODO == "legado" and g.PAINEL is None and g.IDENTIDADE is None
    originais = (g.Painel, g.TokenDoPainel, g.requests.request)
    g.Painel = perigo("Painel")
    g.TokenDoPainel = perigo("TokenDoPainel")
    g.requests.request = perigo("requests.request")   # o guardian so usa isso para falar com o painel
    threads = threading.active_count()
    try:
        assert isinstance(g.conectar().auth_manager, SpotifyOAuth)
        assert rodar_main(SE(503, -1, "x"), n=3) == [60, 60, 60]   # time.sleep exato, como antes
        assert rodar_main(requests.exceptions.ReadTimeout("x"), n=2) == [20, 20]
        g.na_thread_principal(None)
        assert g.PAINEL is None and threading.active_count() == threads
    finally:
        g.Painel, g.TokenDoPainel, g.requests.request = originais


@teste
def t_segredos_nunca_no_log():
    # pareamento, token, heartbeat, comandos e erros do Spotify: nada de segredo no guardian.log
    pf = PainelFalso()
    spotify = Servidor({"/v1/me/player": (401, JSON, {"error": {"status": 401, "message": "Invalid access token"}})})
    try:
        with ModoPainel(pf.url):
            tamanho = os.path.getsize(g.LOG_FILE)
            assert g.parear_pc(CODIGO, pf.url)[0] == 0
            pf.forcado[PAREAR] = (404, {"codigo": "codigo_invalido"})
            assert g.parear_pc(CODIGO, pf.url)[0] == 3
            g.IDENTIDADE = g.ler_identidade()
            conectar = g.conectar

            def conectar_teste():
                sp = conectar()
                sp.prefix = spotify.url + "/v1/"
                return sp
            config = dict(CONFIG_PAINEL, horarioInicio=None, horarioFim=None)
            rodar_main_painel(None, n=2, config=config, conectar=conectar_teste)
            pf.forcado[TOKEN] = (409, {"codigo": "reautorizar", "motivo": "reautorizar"})
            rodar_main_painel(None, n=1, config=config, conectar=conectar_teste)
            g.PAINEL = g.Painel(g.IDENTIDADE)
            entregar(pf, {"id": "c-log", "tipo": "enviar_log", "criadoEm": SERVIDOR_MS})
            novo = log_desde(tamanho)
            assert "PC conectado ao painel" in novo and "recusou o token" in novo and "reautorizar" in novo
            for segredo in (TOKEN_PC, ACESSO, CODIGO):
                assert segredo not in novo, segredo
            for d in pf.s.detalhes:   # nem no que foi mandado ao painel (fora o proprio pareamento)
                corpo = json.dumps(d["corpo"])
                assert TOKEN_PC not in corpo and ACESSO not in corpo
                assert d["caminho"] == PAREAR or CODIGO not in corpo
    finally:
        pf.fechar()
        spotify.fechar()


# ------------------------------------------------------------ sem conta e diagnostico
@teste
def t_modo_sem_conta_mantem_spotify_e_espera_pareamento():
    with ModoPainel():
        g.MODO, g.IDENTIDADE, g.PAINEL = "sem_conta", None, None
        reset()
        relogio = RelogioFalso(parar_depois=2)
        g.time.sleep = relogio
        abertos, reinicios = [], []
        g.spotify_aberto = lambda: False
        g.abrir_spotify = lambda: abertos.append(1) or True

        def reiniciar_falso(motivo):
            reinicios.append(motivo)
            raise Pare()
        g.reiniciar_processo = reiniciar_falso
        g.ATUALIZACAO_ATRASO = 10 ** 9
        try:
            g.modo_sem_conta()
        except Pare:
            pass
        assert abertos == [1] and relogio.esperas == [10, 30] and reinicios == []
        g.time.sleep = RelogioFalso()
        g.gravar_identidade("https://musica.postogef.com.br", "pc1", "Caixa 1", TOKEN_PC)   # pareou
        try:
            g.modo_sem_conta()
        except Pare:
            pass
        assert reinicios == ["PC conectado ao painel"]
        # main() no modo sem conta: nunca conecta no Spotify, vai para o modo_sem_conta
        chamadas = []
        g.modo_sem_conta = lambda: chamadas.append(1)
        g.conectar = perigo("conectar")
        g.ja_esta_rodando = lambda: False
        g.iniciar_vigia_de_antigos = lambda: None
        g.main()
        assert chamadas == [1]


@teste
def t_diagnostico_no_modo_painel_sem_token():
    diag = os.path.join(os.path.dirname(ORIGEM), "diagnostico.py")
    assert os.path.exists(diag), diag
    pf = PainelFalso()
    try:
        pasta = pasta_guardian()
        shutil.copy(diag, pasta)
        identidade_em(pasta, pf.url)
        with open(os.path.join(pasta, "painel_estado.json"), "w", encoding="utf-8") as f:
            json.dump({"config": pf.config, "versaoConfig": 7}, f)
        r = subprocess.run([sys.executable, os.path.join(pasta, "diagnostico.py")], cwd=pasta,
                           input=b"\n" * 5, timeout=120, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        saida = r.stdout.decode("cp1252", errors="replace")
        assert "Modo    : painel" in saida and pf.url in saida and "Caixa 1" in saida, (saida, r.stderr)
        assert CONFIG_PAINEL["playlistUri"] in saida
        assert TOKEN_PC not in saida and ACESSO not in saida
        assert len(pf.pedidos(TOKEN)) == 1   # o login do Spotify veio do painel
    finally:
        pf.fechar()


# ------------------------------------------------------------
def main():
    print("Python %s | guardian %s | %s" % (sys.version.split()[0], g.VERSAO, ORIGEM))
    falhas = 0
    for fn in RESULTADOS:
        try:
            fn()
            print("  ok    " + fn.__name__)
        except BaseException as e:
            falhas += 1
            print("  FALHA %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("%d/%d testes ok" % (len(RESULTADOS) - falhas, len(RESULTADOS)))
    shutil.rmtree(PASTA, ignore_errors=True)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
