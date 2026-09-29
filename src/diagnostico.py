"""
Diagnostico - Spotify Guardian GEF
Mostra em qual conta o login esta, se e premium, e quais
aparelhos (dispositivos) a API enxerga nesta maquina.

Rode na mesma pasta do config.json:
   py -3.8 diagnostico.py      (Windows 7)
   python diagnostico.py       (Windows 10)
"""

import os
import sys
import json
import socket
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Corrige erro quando o nome do computador tem acento (byte 0xe3 = "a" com til).
_getfqdn_orig = socket.getfqdn
def _getfqdn_seguro(nome=""):
    try:
        return _getfqdn_orig(nome)
    except Exception:
        return "localhost"
socket.getfqdn = _getfqdn_seguro

if getattr(sys, "frozen", False):
    PASTA = os.path.dirname(sys.executable)
else:
    PASTA = os.path.dirname(os.path.abspath(__file__))


def ler_config(caminho):
    # Aceita config.json salvo pelo Bloco de Notas em "ANSI", UTF-8 (com ou sem BOM) ou "Unicode".
    with open(caminho, "rb") as f:
        bruto = f.read()
    if bruto[:2] in (b"\xff\xfe", b"\xfe\xff"):
        texto = bruto.decode("utf-16")
    else:
        try:
            texto = bruto.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = bruto.decode("cp1252", errors="replace")
    return json.loads(texto)


try:
    CFG = ler_config(os.path.join(PASTA, "config.json"))
except Exception as e:
    print("ERRO no config.json:", type(e).__name__, e)
    input("\nEnter pra sair...")
    sys.exit(1)

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CFG["client_id"],
    client_secret=CFG["client_secret"],
    redirect_uri=CFG["redirect_uri"],
    scope="user-read-playback-state user-modify-playback-state",
    cache_path=os.path.join(PASTA, ".cache_gef"),
    open_browser=True,
    requests_timeout=(10, 20),
    requests_session=False,
), requests_timeout=(10, 20), requests_session=False)


def versao(pacote):
    try:
        from importlib.metadata import version
        return version(pacote)
    except Exception:
        return "?"


print("=" * 55)
print("Python  :", sys.version.split()[0], " | spotipy", versao("spotipy"),
      "| requests", versao("requests"), "| urllib3", versao("urllib3"))
print("Este PC :", socket.gethostname(), "/", os.environ.get("COMPUTERNAME", ""))
print("Playlist:", CFG.get("playlist_uri"))
print("-" * 55)
try:
    me = sp.me()
    print("Conta logada (do .cache_gef):")
    print("   Nome :", me.get("display_name"))
    print("   ID   :", me.get("id"))
    if me.get("product"):
        print("   Plano:", me.get("product"), "  (precisa ser 'premium')")
    else:
        print("   Plano: (a API nao informa mais; confira em spotify.com/account - precisa ser Premium)")
except Exception as e:
    print("Erro ao identificar a conta:", e)
    input("\nEnter pra sair...")
    sys.exit()

print("-" * 55)
try:
    devs = sp.devices().get("devices", [])
    if not devs:
        print("NENHUM dispositivo encontrado.")
        print()
        print("Checar:")
        print(" 1) O app do Spotify nesta maquina esta ABERTO e")
        print("    logado NESTA MESMA conta acima?")
        print(" 2) Toque uma musica na mao 1 vez e rode este teste de novo.")
        print(" 3) Se a conta acima nao e a deste posto, apague o")
        print("    arquivo .cache_gef e rode de novo pra logar certo.")
    else:
        print("Dispositivos encontrados:")
        for d in devs:
            print(f"   - {d['name']}  (ativo={d['is_active']}, tipo={d['type']}, "
                  f"restrito={d.get('is_restricted')}, id={d.get('id')})")
except Exception as e:
    print("Erro ao listar dispositivos:", e)

print("-" * 55)
try:
    pb = sp.current_playback()
    if not pb:
        print("Nada tocando e nenhum aparelho ativo agora.")
    else:
        dev = pb.get("device") or {}
        print("Reproducao atual:")
        print("   Tocando :", pb.get("is_playing"))
        print("   Aparelho:", dev.get("name"), f"(tipo={dev.get('type')}, id={dev.get('id')})")
        print("   Contexto:", (pb.get("context") or {}).get("uri"))
except Exception as e:
    print("Erro ao ler a reproducao atual:", e)

print("=" * 55)
input("\nEnter pra sair...")
