# Spotify Guardian GEF

Mantém a playlist do GEF sempre tocando no PC da loja: reabre o Spotify se ele
fechar, retoma a playlist se a música parar e se atualiza sozinho pelo GitHub.

Roda no **Windows 7 SP1 (64 bits)** e no **Windows 10/11**. O instalador já traz o
Python junto (3.8 no Windows 7/8, 3.14 no Windows 10/11): não precisa instalar
nada antes.

---

## Instalar numa loja

1. Baixe o **SpotifyGuardian-Setup.exe** da página de releases do projeto
   (`https://github.com/Postogef/spotify-guardian/releases/latest`).
2. Rode o arquivo. Se aparecer "O Windows protegeu o computador", clique em
   **Mais informações → Executar assim mesmo** (o instalador não é assinado).
3. O instalador:
   - instala em `%LOCALAPPDATA%\SpotifyGuardian` (não pede administrador);
   - fecha o guardian antigo, se houver, e **reaproveita o `config.json` e o
     login** de uma instalação antiga (as pastas copiadas à mão, com
     `iniciar.bat`/`guardian.exe`), desativando o atalho antigo da inicialização;
   - numa máquina sem instalação anterior, pede **Client ID, Client Secret e a
     playlist** do posto (painel do Spotify for Developers);
   - coloca o guardian na inicialização do Windows e já deixa rodando.
4. **Primeiro uso:** se ainda não houver login, o navegador abre. Entre com a
   conta Spotify **daquele posto** e autorize. O login fica salvo.

Depois disso não precisa fazer mais nada: o guardian confere **1 vez por dia**
se existe versão nova no GitHub e se atualiza sozinho, sem perder a
configuração nem o login.

### Atalhos no menu Iniciar → Spotify Guardian

| Atalho | Para quê |
|---|---|
| Ver log do Spotify Guardian | Abre o `guardian.log` |
| Diagnóstico do Spotify | Mostra conta, aparelhos e o que está tocando |
| Refazer login do Spotify | Quando o log disser **LOGIN EXPIRADO** (o Spotify exige novo login a cada 6 meses). O login antigo só é trocado se o novo der certo |
| Reiniciar Spotify Guardian | Depois de editar o `config.json` |
| Editar configuração (config.json) | Abre o `config.json` no Bloco de Notas |

### config.json

| Chave | Padrão | O que faz |
|---|---|---|
| `client_id`, `client_secret` | — | App do posto no Spotify for Developers |
| `playlist_uri` | — | `spotify:playlist:ID` (o link com `?si=` também é aceito) |
| `check_interval` | 180 | Segundos entre verificações (mínimo 60) |
| `startup_delay` | 60 | Espera ao ligar o PC |
| `horario_inicio`, `horario_fim` | `null` | Janela em que toca (ex.: 8 e 22; 18 e 2 atravessa a meia-noite). `null` = 24h |
| `dispositivo` | nome do PC | Nome do aparelho da loja no Spotify Connect, se for diferente do nome do computador |
| `fechar_popup` | só no Win7 | Fecha o popup "versão desatualizada" do Spotify no Windows 7 |
| `atualizacao_automatica` | `true` | `false` desliga a atualização automática nesta loja |

---

## Publicar uma versão nova (quem mantém o projeto)

Precisa: Windows 10/11, [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`) e um Python 3 no PATH (só para o `pip`
baixar as bibliotecas).

1. Altere o código em `src\` e aumente `VERSAO` em `src\guardian.py`
   (ex.: `2.0.0` → `2.0.1`).
2. Gere o instalador:
   ```powershell
   .\build.ps1 -Repo Postogef/spotify-guardian
   ```
   O build baixa os Pythons embutidos (com SHA-256 conferido), instala as
   bibliotecas com versões fixas (`requirements-py38.txt`,
   `requirements-py314.txt`), roda os testes nos dois Pythons e gera
   `dist\SpotifyGuardian-Setup.exe` + `dist\SpotifyGuardian-Setup.exe.sha256`.
   Para um build só de teste, sem atualização automática: `.\build.ps1 -Local`.
3. No GitHub, crie um **release** com a tag `v<VERSAO>` (ex.: `v2.0.1`) e anexe
   os **dois** arquivos de `dist\`. As lojas se atualizam em até ~24 h.

Regras de segurança da atualização:
- O repositório precisa ser **público** (as lojas consultam o GitHub sem senha).
- O guardian só instala se o SHA-256 do `.exe` bater com o `.sha256` do release,
  ignora releases marcados como *pre-release* e nunca instala versão menor ou
  igual à atual.
- O mesmo instalador roda no máximo 2 vezes numa loja (1 por dia): um release
  com problema não fica reinstalando sem parar.
- Use **autenticação em dois fatores** na conta do GitHub: quem publica um
  release consegue instalar programas em todas as lojas.
- Para desfazer uma versão ruim, publique uma versão **maior** com o código
  anterior (ex.: `v2.0.2` com o conteúdo da `v2.0.0`).

### Estrutura

```
src\guardian.py            o programa
src\diagnostico.py         diagnóstico (conta, aparelhos, reprodução)
instalador\SpotifyGuardian.iss   script do Inno Setup
instalador\parar.ps1       fecha guardians em execução (PowerShell 2.0+)
instalador\migrar.ps1      acha e migra instalações antigas (PowerShell 2.0+)
testes\testar_guardian.py  testes offline (rodam nos dois Pythons no build)
build.ps1                  gera o instalador
```

> As pastas `SpotifyGuardian Gef 1/2/3` são as instalações antigas de cada posto
> e têm `config.json` com senhas. Elas estão no `.gitignore` e **nunca** devem
> ir para o GitHub.

### Windows 7

- Precisa do Service Pack 1 e da atualização **KB2999226** (Universal C Runtime).
  Quem já rodava o Python 3.8 tem as duas; o instalador avisa se faltar.
- Os scripts do instalador são compatíveis com o PowerShell 2.0 que vem no
  Windows 7.
