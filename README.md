# Spotify Guardian GEF

Mantém a playlist do GEF sempre tocando no PC da loja: reabre o Spotify se ele
fechar, retoma a playlist se a música parar e se atualiza sozinho pelo GitHub.

A partir da 2.1.0 o PC pode ser **conectado ao painel Música GEF**
(`https://musica.postogef.com.br`): a playlist, o horário e o login do Spotify passam
a ser controlados pelo painel (ver [Modo painel](#modo-painel-música-gef)). Sem
pareamento, o `config.json` com `client_id` continua funcionando exatamente como antes
(modo antigo).

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
   - pede o **código de 6 dígitos** do painel Música GEF (página do PC no painel) e,
     depois de copiar os arquivos, conecta o PC ao painel. Se o código estiver errado,
     vencido ou faltar internet, abre uma janela para digitar outro (**Conectar**) ou
     seguir sem o painel (**Pular**). A página não aparece se o PC já está conectado.
     Numa loja que já usa o modo antigo, deixe o código em branco para continuar como
     está;
   - quem não usa o painel marca **Instalar sem o painel (avançado: Client ID e Client
     Secret)**: numa máquina sem instalação anterior, o instalador pede **Client ID,
     Client Secret e a playlist** do posto (painel do Spotify for Developers), como antes;
   - coloca o guardian na inicialização do Windows e já deixa rodando;
   - só para o guardian antigo depois que o novo abriu. Se o novo não abrir, o
     antigo continua funcionando e o instalador avisa.

   O instalador **não** roda como administrador. A única exceção: se o guardian
   antigo estiver na inicialização de **todos os usuários**, o Windows pede a
   permissão de administrador só para tirar esse atalho. Se ninguém aceitar,
   nada quebra: o guardian novo fecha o antigo sozinho a cada login.
4. **Primeiro uso (só no modo antigo):** se ainda não houver login, o navegador abre.
   Entre com a conta Spotify **daquele posto** e autorize. O login fica salvo. No modo
   painel o navegador nunca abre: a conta é conectada no painel.

A tela final do instalador diz se o PC ficou conectado ao painel.

O guardian roda sem janela (aparece como `pythonw.exe` no Gerenciador de Tarefas) e
inicia junto com o Windows, quando o usuário entra. Ele também cria uma **tarefa
agendada** só desse usuário (sem administrador) que confere a cada 5 minutos e
reabre o guardian se ele tiver sido fechado.

Depois disso não precisa fazer mais nada: o guardian confere **1 vez por dia**
se existe versão nova no GitHub e se atualiza sozinho, sem perder a
configuração nem o login.

### Instalação silenciosa (em lote)

```
SpotifyGuardian-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /CODIGO=123456
```

`/CODIGO=` conecta o PC ao painel sem abrir nenhuma janela (sem internet, tenta 3
vezes). Sem `/CODIGO=` a instalação silenciosa nunca para pedindo código (é assim que
roda a atualização automática). Códigos de saída do instalador:

| Código | Significado |
|---|---|
| 0 | Instalado |
| 8 | Instalado, mas o pareamento pedido (`/CODIGO=` ou o código digitado) não deu certo |
| 9 | O guardian novo não abriu: a versão anterior voltou (ou o guardian antigo foi mantido) |

### Atalhos no menu Iniciar → Spotify Guardian

| Atalho | Para quê |
|---|---|
| Ver log do Spotify Guardian | Abre o `guardian.log` |
| Diagnóstico do Spotify | Mostra conta, aparelhos e o que está tocando |
| Conectar ao painel | Pede o código de 6 dígitos gerado no painel e conecta este PC (troca a conexão se o PC já estava conectado). Depois reinicia o guardian |
| Refazer login do Spotify | Modo antigo: quando o log disser **LOGIN EXPIRADO** (o Spotify exige novo login a cada 6 meses). O login antigo só é trocado se o novo der certo. No modo painel só orienta: a conta é reconectada no painel |
| Reiniciar Spotify Guardian | Depois de editar o `config.json` |
| Editar configuração (config.json) | Abre o `config.json` no Bloco de Notas |

### Modo painel (Música GEF)

- **Como conectar:** no painel, crie o PC (ou gere um código novo na página dele) e
  digite o código de 6 dígitos no instalador ou no atalho **Conectar ao painel**. O
  código vale 24 horas e só pode ser usado uma vez.
- O pareamento grava o `painel.json` na pasta do guardian: endereço do painel, id e
  nome do PC e o token do PC protegido pelo Windows (DPAPI: só abre neste usuário,
  neste computador). Ele nunca vai para o `config.json`. Se ainda não houver
  `config.json`, o pareamento cria um só com `startup_delay`.
- Vêm do painel: playlist, embaralhar, horário, intervalo de verificação, nome do
  aparelho, atualização automática e reabrir sozinho. O login do Spotify também fica
  no painel: o PC só recebe um access token, nunca a senha nem o refresh token.
- Continuam no `config.json` do PC (o painel não mexe): `startup_delay`,
  `fechar_popup`, `url_atualizacao` e `atualizacao_atraso_s`. As credenciais do modo
  antigo ficam lá, mas não são usadas enquanto o PC estiver conectado.
- O PC manda o que está tocando a cada 5 minutos, consulta novidades a cada 60
  segundos e recebe comandos do painel (reabrir o Spotify, reiniciar, buscar
  atualização, enviar o log). Com o painel fora do ar a música continua.
- Se o PC for desvinculado no painel (confirmado 3 vezes em pelo menos 10 minutos), o
  `painel.json` vira `painel.json.revogado` e o guardian só mantém o Spotify aberto até
  um pareamento novo. Ele **nunca** volta sozinho para o modo antigo.
- `guardian.py --desparear` desconecta o PC (só para suporte); o `painel.json` também
  vira `painel.json.revogado`.
- Com o `painel.json.revogado` na pasta, o PC fica esperando um pareamento novo
  **mesmo que o `config.json` tenha o `client_id` do modo antigo**. Para conectar de
  novo, basta um código novo do painel. Para voltar ao modo antigo, o suporte apaga o
  `painel.json.revogado` à mão e reinicia o guardian.

### config.json

As chaves marcadas com * só valem no modo antigo; no modo painel elas vêm do painel.

| Chave | Padrão | O que faz |
|---|---|---|
| `client_id`, `client_secret` * | — | App do posto no Spotify for Developers |
| `playlist_uri` * | — | `spotify:playlist:ID` (o link com `?si=` também é aceito) |
| `check_interval` * | 180 | Segundos entre verificações (mínimo 60) |
| `startup_delay` | 60 | Espera ao ligar o PC |
| `horario_inicio`, `horario_fim` * | `null` | Janela em que toca (ex.: 8 e 22; 18 e 2 atravessa a meia-noite). `null` = 24h |
| `dispositivo` * | nome do PC | Nome do aparelho da loja no Spotify Connect, se for diferente do nome do computador |
| `fechar_popup` | só no Win7 | Fecha o popup "versão desatualizada" do Spotify no Windows 7 |
| `atualizacao_automatica` * | `true` | `false` desliga a atualização automática nesta loja |
| `reabrir_sozinho` * | `true` | Tarefa agendada do Windows que reabre o guardian em até 5 minutos se ele for fechado. `false` remove a tarefa |

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
  anterior (ex.: `v2.0.4` com o conteúdo da `v2.0.3`). Não volte para um código
  **anterior à 2.0.2**: ele não conhece a tarefa agendada, que ficaria para trás
  nas lojas. Para desligar a tarefa em todas as lojas, publique uma versão com o
  padrão de `reabrir_sozinho` em `false` (o guardian remove a tarefa ao abrir).
- Depois que alguma loja for conectada ao painel sem ter o `config.json` do modo
  antigo (com `client_id`), **nunca publique código anterior à 2.1.0**: ele não
  conhece o `painel.json` e essa loja pararia de tocar.

### Estrutura

```
src\guardian.py            o programa
src\diagnostico.py         diagnóstico (conta, aparelhos, reprodução)
instalador\SpotifyGuardian.iss   script do Inno Setup
instalador\parar.ps1       fecha guardians em execução (PowerShell 2.0+)
instalador\migrar.ps1      acha e migra instalações antigas (PowerShell 2.0+)
testes\testar_guardian.py  testes offline (rodam nos dois Pythons no build)
testes\vetores\uris-v1.json  casos de URI de playlist (cópia igual à do painel)
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
