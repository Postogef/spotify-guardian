"""
Diagnostico - Spotify Guardian GEF
Mostra em qual conta o login esta, se e premium, e quais
aparelhos (dispositivos) a API enxerga nesta maquina.

Rode na mesma pasta do guardian.py (config.json / painel.json):
   py -3.8 diagnostico.py      (Windows 7)
   python diagnostico.py       (Windows 10)

Usa o proprio guardian.py: no modo painel o login vem do painel (TokenDoPainel)
e o token nunca aparece na tela.
"""

import os
import sys
import socket

if getattr(sys, "frozen", False):
    PASTA = os.path.dirname(sys.executable)
else:
    PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)  # o Python embutido nao poe a pasta do script no caminho

try:
    import guardian  # le o config.json / painel.json e corrige o nome do PC com acento
except Exception as e:
    print("ERRO no config.json:", type(e).__name__, e)
    input("\nEnter pra sair...")
    sys.exit(1)

print("=" * 55)
print("Python  :", sys.version.split()[0], " | spotipy", guardian.versao("spotipy"),
      "| requests", guardian.versao("requests"), "| urllib3", guardian.versao("urllib3"))
print("Este PC :", socket.gethostname(), "/", os.environ.get("COMPUTERNAME", ""))
if guardian.MODO == "painel":
    guardian.PAINEL = guardian.Painel(guardian.IDENTIDADE)
    config, versao_cfg = guardian.PAINEL.config_em_cache()
    config = config or {}
    print("Modo    : painel (login do Spotify feito no painel)")
    print("Painel  :", guardian.IDENTIDADE["url"])
    print("PC      :", guardian.IDENTIDADE["pcNome"])
    print("Playlist:", config.get("playlistUri"),
          f"({config.get('playlistNome')})" if config.get("playlistNome") else "",
          f"[configuracao versao {versao_cfg}]")
elif guardian.MODO == "sem_conta":
    print("Modo    : sem conta (este PC nao esta conectado ao painel)")
    print("Conecte: menu Iniciar > Spotify Guardian > 'Conectar ao painel'.")
    input("\nEnter pra sair...")
    sys.exit()
else:
    print("Modo    : legado (config.json com client_id)")
    print("Playlist:", guardian.CFG.get("playlist_uri"))
print("-" * 55)

sp = guardian.conectar()
try:
    me = sp.me()
    print("Conta logada (" + ("do painel" if guardian.MODO == "painel" else "do .cache_gef") + "):")
    print("   Nome :", me.get("display_name"))
    print("   ID   :", me.get("id"))
    if me.get("product"):
        print("   Plano:", me.get("product"), "  (precisa ser 'premium')")
    else:
        print("   Plano: (a API nao informa mais; confira em spotify.com/account - precisa ser Premium)")
except Exception as e:
    print("Erro ao identificar a conta:", guardian.resumo_erro(e))
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
        if guardian.MODO == "painel":
            print(" 3) Se a conta acima nao e a deste posto, troque a conta")
            print("    deste PC no painel.")
        else:
            print(" 3) Se a conta acima nao e a deste posto, apague o")
            print("    arquivo .cache_gef e rode de novo pra logar certo.")
    else:
        print("Dispositivos encontrados:")
        for d in devs:
            print(f"   - {d['name']}  (ativo={d['is_active']}, tipo={d['type']}, "
                  f"restrito={d.get('is_restricted')}, id={d.get('id')})")
except Exception as e:
    print("Erro ao listar dispositivos:", guardian.resumo_erro(e))

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
    print("Erro ao ler a reproducao atual:", guardian.resumo_erro(e))

print("=" * 55)
input("\nEnter pra sair...")
