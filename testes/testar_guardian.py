"""
Testes offline do guardian.py. Roda no Python embutido (3.8 e 3.14) pelo build.ps1:
    python testar_guardian.py <caminho do guardian.py>

Nada sai para a internet: qualquer conexao fora de 127.0.0.1 e bloqueada
(proxy invalido). Nao abre o Spotify, nao fecha janelas, nao roda instalador.
"""
import os
import sys
import json
import time
import shutil
import hashlib
import tempfile
import threading
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


# ------------------------------------------------------------ main (tratamento de erros)
def rodar_main(erro, n=1):
    reset()
    relogio = RelogioFalso(parar_depois=n)
    g.time.sleep = relogio
    g.ATUALIZACAO_ATRASO = 10 ** 9
    originais = (g.garantir_musica, g.conectar, g.ja_esta_rodando)

    def falha(sp):
        raise erro
    g.garantir_musica = falha
    g.conectar = lambda: object()
    g.ja_esta_rodando = lambda: False
    try:
        g.main()
    except Pare:
        pass
    finally:
        g.garantir_musica, g.conectar, g.ja_esta_rodando = originais
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


# ------------------------------------------------------------ servidor local (API falsa)
class Servidor:
    def __init__(self, rotas):
        self.rotas = rotas
        self.pedidos = []
        servidor = self

        class H(BaseHTTPRequestHandler):
            def _responder(self):
                servidor.pedidos.append((self.command, self.path))
                caminho = self.path.split("?")[0]
                status, cabecalhos, corpo = servidor.rotas.get(caminho, (404, {}, b"{}"))
                if callable(corpo):
                    corpo = corpo()
                self.send_response(status)
                for k, v in cabecalhos.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
            do_GET = do_PUT = do_POST = _responder

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
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
