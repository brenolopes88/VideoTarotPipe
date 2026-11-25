# PIPE_CONTEXT - Pipeline do Oraculo

Documento de referencia do comportamento implementado no workspace em 2026-09-23. O fluxo ativo e o `studio/app_2.py`.

## 1. Visao Geral da Arquitetura

### Plataforma e orquestracao

- Linguagem: Python.
- Orquestrador/interface: Streamlit, executado localmente.
- Fluxo ativo: `studio/app_2.py`.
- Estado das filas: `studio/fila_state.py`, mantido em `st.session_state` durante a sessao do Streamlit.
- Gerador de roteiro conectado ao estúdio: `gerador_gemini.py`.
- Modulos de pos-processamento:
  - `scripts/voz.py`: sintese de voz com Coqui XTTS v2.
  - `scripts/video_v2.py`: composicao e renderizacao com MoviePy, Pillow e OpenCV.
  - `scripts/utils.py`: descoberta de cartas, segmentacao do texto, pausas e gerador de legenda social.

Nao ha, no codigo encontrado, um orquestrador externo como n8n ou Make, webhook HTTP, endpoint de API proprio ou cron/job agendado. O disparo e manual pela interface: o usuario preenche a grade, adiciona itens a fila e clica em **Iniciar producao**.

A forma esperada de iniciar a interface e equivalente a:

```powershell
streamlit run studio/app_2.py
```

O README descreve a mesma arquitetura geral, mas este documento considera como fonte de verdade o caminho executado pelo `studio/app_2.py` e seus imports.

### Filas atuais

A fila e dividida em tres tipos:

1. `fila_roteiros`: itens com cartas, tipo, persona, elemento opcional, data e modelo.
2. `fila_vozes`: roteiro, nome do projeto, banco de referencia, temperatura e velocidade.
3. `fila_receitas`: roteiro, WAV, backgrounds por bloco e musica.

Ao clicar em **Iniciar producao**, o app processa sequencialmente todas as filas nesta ordem: roteiros, vozes e receitas de video. Cada item e removido da fila ao final da tentativa, mesmo quando ocorre erro; falhas sao registradas no log e exibidas na interface quando aplicavel.

## 2. Fluxo de Dados e Etapas do Pipeline

### Etapa 1 - Entrada das cartas e contexto

1. O app lista os arquivos de imagem existentes em `inputs/cartas/` (`.jpg`, `.jpeg`, `.png` e `.webp`) e usa o nome do arquivo sem extensao como nome da carta.
2. Na aba **Gerador em Lote**, o usuario adiciona linhas dinamicas com data `DD/MM`, tipo, persona, cartas e elemento opcional.
  - Tipos: `MANHA`, `NOITE`, `ROMANTICA`, `FINANCAS`, `ICHING` e `ELEMENTOS`.
  - Personas Tarot: `TERNA`, `MEIO-TERMO` ou `CONFRONTADORA`; `MEIO-TERMO` e o padrao.
  - `ELEMENTOS` exige um dos elementos `Fogo`, `Terra`, `Ar` ou `Agua`.
  - A interface valida a quantidade de cartas e que a selecao corresponda a fonte definida pelo tipo.
3. Nao existe sorteio automatico de cartas no fluxo de roteiro. As cartas sao selecionadas pelo usuario. O sorteio existente e de backgrounds e musica no modo **Express**.
4. Ao adicionar o lote, cada linha valida apenas a presenca de data e cartas e vira um `ItemFilaRoteiro` em `st.session_state`.

### Etapa 2 - Geracao do roteiro

Para cada item da fila, `studio/app_2.py` chama:

```python
solicitar_roteiro(
    cartas=cartas_list,
    data_ddmm=item.data,
  tipo=item.tipo,
  persona=item.persona,
  elemento=item.elemento,
  linha_mutante=item.linha_mutante
)
```

O gerador:

1. Carrega `inputs/tarot_knowledge.json` com `st.cache_data`.
2. Para cada carta, procura primeiro uma chave exata em minusculas e depois uma correspondencia parcial.
3. Converte as definicoes encontradas em um bloco Markdown com Essencia, Luz, Sombra e Conselho.
4. Resolve `TIPOS[tipo]`, valida a quantidade de cartas, deriva a fonte e monta o prompt com tipo, persona, data, definicoes e, em `ELEMENTOS`, o elemento escolhido.
5. Para Tarot, sorteia uma lente emocional (`FERIDA`, `OPORTUNIDADE` ou `JORNADA`) com pesos 33/33/34, excluindo a última lente de roteiro Tarot salvo com sucesso. A reserva não atualiza o estado: `estado_lente.json` só é atualizado depois de JSON e legenda gravados; o arquivo é ignorado pelo Git. I Ching não sorteia lente.
6. Chama a API Google Gemini pelo SDK `google-genai`:
   - modelo configurado: `gemini-3.5-flash`;
   - `system_instruction`: `SYSTEM_PROMPT` de `gerador_gemini.py`;
   - resposta: `application/json`;
   - schema estruturado: `schema_roteiro`;
   - temperatura: `0.78`.
7. Faz `json.loads(response.text)` e obtém um roteiro e uma legenda únicos; Tarot recebe também o campo opcional `lente`. O estúdio exibe esse valor na coluna Lente e o registra no log da geração; I Ching é identificado como prompt próprio e roteiros antigos sem metadado aparecem como não registrados.
8. Grava o JSON e a legenda na pasta do projeto.

A chave e lida de `GEMINI_API_KEY`. Se a variavel nao existir, o codigo usa literalmente o placeholder `Your_key_here`, o que normalmente faz a chamada falhar; nao ha leitura de `.env` no caminho ativo.

### Etapa 3 - Persistencia do roteiro

Para uma data de quatro digitos, a normalizacao e:

- `DD/MM` ou `DD-MM` -> `DD-MM`;
- o sufixo vem de `TIPOS[tipo]["sufixo"]`: `M`, `N`, `R`, `F`, `I` ou `E`.

O resultado e salvo em:

```text
projects/<DD-MM-{sufixo}>/<DD-MM-{sufixo}>.json
projects/<DD-MM-{sufixo}>/legenda.txt
```

Exemplo: `projects/15-09-R/15-09-R.json` para `ROMANTICA`.
O JSON preserva a estrutura consumida pelo estúdio: `id`, `roteiro` e `legenda`.

A constante `GERADOS_DIR` existe em `gerador_gemini.py`, mas nao participa do salvamento efetivo.

### Etapa 4 - Geracao de voz

Depois de selecionar um ou mais roteiros, o estudio permite escolher um banco de referencia e adicionar uma voz por roteiro a fila. Cada texto de bloco e enviado ao `gerar_audio_oraculo` em `scripts/voz.py`.

- Modelo: `tts_models/multilingual/multi-dataset/xtts_v2`.
- Engine: Coqui TTS/XTTS v2.
- Dispositivo: CUDA se disponivel e com capacidade minima 6; caso contrario CPU.
- Idioma: `pt`.
- Referencias: subpastas com WAVs em `inputs/voz/`; pastas sem WAV nao aparecem na interface.
- Banco atual padrao: `inputs/voz/oraculo_original/`, com temperatura `0.88` e velocidade `1.12`.
- O banco `inputs/voz/teste/` permanece oculto enquanto estiver vazio.
- Cada banco deve conter entre 5 e 7 arquivos WAV de referencia.
- O texto e dividido por pontuacao em `scripts/utils.py` antes da sintese.
- Cada chunk recebe pausas configuradas e passa por `scripts/audio_fx.py`.
- `temperature`, `top_p=0.85` e `speed` sao enviados ao XTTS.

O WAV e salvo pelo modulo de voz em `projects/<nome_projeto>/`, com nome baseado no projeto e banco de referencia.

### Etapa 5 - Escolha de arte e montagem da receita

O usuario pode escolher backgrounds por bloco na aba de direcao de arte ou usar **Express**:

- backgrounds: arquivos `.mp4` em `inputs/videos_fundo/`;
- musicas: `.mp3` e `.wav` em `inputs/audio_assets/`;
- Express sorteia backgrounds e uma musica, mas nao sorteia cartas;
- e criado um `Receita` com um background por bloco, WAV, roteiro e volume da musica.

A receita e adicionada a `fila_receitas` e processada somente quando o usuario inicia a producao.

### Etapa 6 - Renderizacao do video

`montar_video_v2` em `scripts/video_v2.py`:

1. Valida o WAV, backgrounds e musica.
2. Usa a duracao do audio como duracao total do video.
3. Divide a duracao entre cenas proporcionalmente ao numero de caracteres do texto.
4. Para cada bloco, cria um fundo vertical 1080x1920, em loop quando necessario.
5. Para `foco`, carrega a imagem da carta em `inputs/cartas/`, exibe o nome e gira a carta 180 graus quando `invertida` e verdadeiro.
6. Para `lado_a_lado`, exibe as cartas lado a lado.
7. Para `cta_integrado`, tambem aceita cartas, embora o schema do gerador ativo use `link_na_bio`.
8. Gera legendas queimadas no video com Whisper `small`, CPU, idioma portugues e timestamps por palavra. Agrupa tres palavras por legenda e posiciona o texto em torno de y=1550.
9. Adiciona logo se `logo_projeto.png` existir na raiz.
10. Mixa a voz com musica opcional, em loop se necessario, com fadeout de dois segundos.
11. Exporta MP4 H.264/AAC, 30 FPS, bitrate `6000k`, em:

```text
projects/<nome_projeto>/morning_review/<nome_projeto>.mp4
```

Se o nome ja existir, acrescenta `_01`, `_02` etc.

Nao ha uma etapa de upload para Instagram, TikTok ou YouTube implementada no repositorio. A entrega automatizada termina nos arquivos locais de roteiro, audio e video.

## 3. Mapeamento de Entradas e Saidas

### Inputs iniciais

O input logico de uma geracao de roteiro e:

| Campo | Tipo | Origem | Uso |
|---|---|---|---|
| `cartas` | `list[str]` | Multiselect da interface | Nomes exatos das cartas |
| `persona` | `str` | Seletor da linha | Variante narrativa usada no prompt |
| `tipo` | `str` | Seletor da linha | Fonte, quantidade de cartas/blocos e sufixo |
| `data_ddmm` | `str` | Campo `DD/MM` | Nome do projeto e contexto |
| `elemento` | `str | None` | Seletor usado por `ELEMENTOS` | Contexto do elemento; obrigatorio somente nesse tipo |
| `linha_mutante` | `bool` | Checkbox usado por `ICHING` | Mapeado ao campo existente `invertida` |
| `GEMINI_API_KEY` | variavel de ambiente | Ambiente local | Autenticacao no Google Gemini |

`fonte` nao e mais um parametro manual: e derivada de `TIPOS[tipo]["fonte"]`. O contexto vem de `inputs/tarot_knowledge.json` ou `inputs/iching_knowledge.json`, conforme essa fonte.

### Payload textual enviado ao Gemini

A chamada envia `SYSTEM_PROMPT` como instrucao de sistema e o seguinte template como `contents`:

```text
Gere um roteiro para as seguintes cartas: {cartas_txt}
Data: {data_ddmm}
Tipo: {tipo}
Gere exatamente {blocos} blocos para {num_cartas} carta(s).
{elemento_instruction}

{definicoes}

INSTRUÇÕES ESPECÍFICAS DE BLOCO:
Use a estrutura de blocos definida para o tipo selecionado.
Fale sempre diretamente com 'você'. Acolha a ferida antes de nomeá-la;
não dramatize, não console de forma vazia — fique ao lado da dor.
No bloco de CTA (link_na_bio), faça uma pergunta afetuosa que convide a
pessoa a se abrir sobre si mesma, terminando com '?'.
```

`{definicoes}` e gerado assim, para cada carta encontrada:

```text
## DEFINIÇÕES DAS CARTAS DESTE ROTEIRO
### <name>
**Essência:** <essence>
**Luz:** <lightMeaning>
**Sombra:** <shadowMeaning>
**Conselho:** <guidanceWhenReversed>
```

### SYSTEM_PROMPT ativo

O prompt ativo de `gerador_gemini.py` e:

```text
Você é o gerador de roteiros do Oráculo, um canal de tarot no TikTok e Instagram.
Seu público é brasileiro. Use português do Brasil em todos os textos.

## IDENTIDADE DA PERSONA (VARIANTE TERNA)
O Oráculo fala com ternura, mas não com bajulação. Ele se aproxima do
espectador como alguém que já chorou a mesma dor e por isso não precisa
dramatizá-la — só reconhecê-la em voz baixa. Ele não nega o que dói, mas
também não deixa o espectador sozinho com a dor: oferece companhia, não
solução. A persona é quente, mas honesta — ela abraça o ponto sensível
antes de nomeá-lo, e só depois de acolher é que revela. Pense numa pessoa
mais velha, cansada de fingir que tudo tem resposta fácil, que ainda assim
escolhe ficar por perto e dizer a verdade com a mão no seu ombro.

## REGRAS DE CONSTRUÇÃO
REGRA 1 — TENSÃO ACOLHIDA: Nomeie o conflito emocional real, mas não o
deixe cru. Ofereça um lugar pra essa dor existir sem precisar ser resolvida
agora — ela pode continuar depois do vídeo, mas o espectador não sai sozinho.
REGRA 2 — FERIDA COM COLO: Toque no ponto sensível da carta com a mesma
precisão das outras variantes, mas envolva o toque em reconhecimento —
"isso dói porque importa", não "isso dói e ponto".
REGRA 3 — CTA QUE GERA COMENTÁRIO: Pergunta afetuosa que convida a pessoa a
se abrir, não a se expor. Ela deve sentir que pode responder com
vulnerabilidade sem se sentir encurralada.
REGRA 4 — DURAÇÃO ALVO: 1 carta (150-200 palavras no total). 2 cartas (250-320 palavras no total).
REGRA 5 — LINGUAGEM: Frases curtas e médias alternadas. Sem adjetivos vazios. Verbos de ação.
REGRA 6 — SOMBRA DA CARTA POSITIVA: Mostre o que ela cobra, revela ou onde
ela engana — mas trate essa sombra como parte do cuidado, não como acusação.

## MECANISMO DE TENSÃO POR TIPO DE CARTA
- MOVIMENTO: Velocidade versus direção errada.
- PODER: Sombra do poder. Quem nutre demais esvazia. Quem controla afasta.
- ESTRUTURA: Sustentação versus prisão disfarçada de segurança.
- ESPERA: Espera legítima versus estagnação disfarçada.
- FIM: O fim aconteceu, mas o espectador não aceitou.
- INÍCIO: O começo foi oferecido, mas há medo de agir.
- ILUSÃO: Intuição ou medo disfarçado de intuição?
- VITÓRIA: Toda vitória tem um preço oculto.

## ESTRUTURA OBRIGATÓRIA DA RESPOSTA
- Para tipos de uma carta: exatamente 6 blocos de foco, com o último como `link_na_bio`.
- Para NOITE (2 cartas): exatamente 8 blocos na ordem: Foco Carta 1, Foco Carta 2,
  Foco Carta 1, Foco Carta 2, Foco Carta 1, Foco Carta 2, `lado_a_lado` sintetizando ambas e `link_na_bio`.
```

### Schema JSON esperado

A resposta deve ser JSON estruturado pelo schema fornecido ao SDK, sem XML ou Markdown externo:

```json
{
  "id": "string",
  "roteiro": [
    {
      "tipo": "foco | lado_a_lado | link_na_bio",
      "carta": "nome da carta ou link_na_bio",
      "cartas": ["nome da carta"],
      "invertida": true,
      "texto": "texto do bloco"
    }
  ],
  "legenda": "Gancho, copy, CTA e hashtags de tarot"
}
```

Detalhes do schema:

- raiz obrigatoria: `id`, `roteiro`, `legenda`;
- bloco obrigatorio: `tipo`, `carta`, `invertida`, `texto`;
- `cartas` e opcional no schema, mas deve ser usado para `lado_a_lado`;
- `legenda` e uma string unica, neutra entre plataformas, pronta para postagem;
- `lente` e uma string opcional: preenchida pelo gerador em Tarot e ausente em I Ching e roteiros legados;
- `id` e descrito no schema no formato `DDMM-P_CARTAS`, mas o codigo nao valida nem reescreve esse valor;
- `tipo` dos blocos gerados nao e validado depois do parse;
- a entrada valida o tipo e a quantidade de cartas, mas o codigo nao valida localmente a quantidade de blocos gerados, a contagem de palavras ou o ponto de interrogacao no CTA.

O JSON e formatado no disco com `ensure_ascii=False` e `indent=2`.

## 4. Tratamento e Integracoes Pos-Geracao

### Parse e tratamento do roteiro

- A resposta textual do Gemini e convertida diretamente com `json.loads`.
- O roteiro e relido pela interface para extrair cartas, quantidade de blocos e textos.
- O editor do Streamlit permite editar o JSON ativo manualmente, desde que o novo conteudo seja JSON valido.
- Nao ha parse de Markdown, XML ou marcadores especiais no retorno do modelo.

### TTS e audio

Cada `texto` do bloco e concatenado para a fila de voz. `dividir_texto` remove quebras de linha, separa por pontuacao e cria chunks de no minimo 15 caracteres e no maximo nominal de 220 caracteres, com tolerancia de 15% em alguns cortes. Pausas configuradas incluem, entre outras:

- `.` e `!`: 400 ms;
- `?`: 400 ms;
- `...`: 800 ms;
- `|`: 2000 ms;
- `;` e `:`: 150 ms.

O audio final e WAV. O modulo alerta quando a duracao ultrapassa 75 segundos por ser potencialmente longo para TikTok, mas nao bloqueia a exportacao.

### Video

O renderizador cria video vertical 9:16 em 1080x1920, com 30 FPS e audio AAC. A duracao e a do WAV; a distribuicao temporal das cenas e proporcional ao tamanho dos textos. O burn-in de legendas usa Whisper `small` localmente e pode ser omitido se Whisper nao estiver disponivel.

### Legenda social

Atualmente nao existe uma etapa ativa de postagem nem uma ramificacao separada para Instagram e TikTok. O mesmo video pode ser exportado para uso manual nas redes.

Uma legenda social unica e gerada pela mesma requisicao do Gemini e salva em
`projects/<PROJETO>/legenda.txt`. Ela combina Gancho, Copy, CTA e hashtags de
tarot, espiritualidade e autoconhecimento, sem ser exclusiva de uma rede.

As duas funcoes abaixo continuam disponiveis como alternativas legadas, mas nao
sao a origem da legenda gerada pelo fluxo atual:

- `scripts/utils.py::gerar_arquivo_legenda`: formato mais simples, com gancho, CTA, chamada para AMEM e hashtags;
- `scripts/video_v2.py::gerar_arquivo_legenda`: formato com gancho, CTA, hashtags base e ate sete hashtags totais.

No final de `montar_video_v2`, a chamada legada que criaria `legenda.txt` continua comentada. Portanto, o renderizador nao substitui as legendas geradas pelo Gemini. Os arquivos `legenda.txt` existentes em `Postados/` sao artefatos historicos ou gerados por outro processo.

Em consequencia:

- Instagram: sem tratamento automatico especifico;
- TikTok: sem tratamento automatico especifico;
- diferencas de limite, copy, hashtags, upload ou agendamento: nao implementadas no codigo atual;
- postagem: manual, fora do pipeline encontrado.

## 5. Regras de Negocio e Restricoes Atuais

### Regras de roteiro

- `MANHA`: 1 carta, 6 blocos, fonte Tarot, sufixo `M`.
- `NOITE`: 2 cartas, 8 blocos, fonte Tarot, sufixo `N`; alterna tres focos por carta, depois `lado_a_lado` e `link_na_bio`.
- `ROMANTICA`: 1 carta, 6 blocos, Tarot, sufixo `R`, tema amoroso independente da persona.
- `FINANCAS`: 1 carta, 6 blocos, Tarot, sufixo `F`, leitura comportamental sem orientacao financeira literal.
- `ICHING`: 1 hexagrama, 6 blocos, conhecimento e prompt I Ching, sufixo `I`.
- `ELEMENTOS`: 1 carta, 6 blocos, Tarot, sufixo `E`; exige `Fogo`, `Terra`, `Ar` ou `Água` e adiciona essa referência ao prompt.
- Tipo desconhecido, contagem incorreta de cartas ou elemento ausente/invalido causa `ValueError`; nao ha fallback silencioso.
- Em Tarot, uma lente `FERIDA`, `OPORTUNIDADE` ou `JORNADA` é sorteada por geração com pesos 33/33/34; retries reutilizam a mesma lente. Apenas gerações Tarot salvas com sucesso avançam `ultima_lente`, que é excluída do próximo sorteio, inclusive entre dias.
- Lente emocional, persona (`TERNA`, `MEIO-TERMO`, `CONFRONTADORA`) e tema (`ROMANTICA`/`FINANCAS`) são instruções separadas. I Ching usa seu prompt próprio e não altera o estado das lentes.
- Cada bloco deve conter texto e indicar `invertida` como booleano.
- O CTA deve ser uma pergunta terminada em `?`, conforme instrucoes do prompt, mas isso nao e verificado localmente.
- Meta de texto: 150-200 palavras para uma carta e 250-320 para duas cartas. E uma instrucao ao Gemini, nao uma validacao no Python.
- O texto deve ser em portugues do Brasil, diretamente dirigido a `voce`, com a persona terna ativa.

### Limites e parametros da LLM

- Modelo: `gemini-3.5-flash`, conforme string configurada no codigo.
- Temperatura: `0.78`.
- `response_mime_type`: `application/json`.
- Schema estruturado: `schema_roteiro`.
- `max_output_tokens`: nao configurado explicitamente.
- Limite de entrada/token total: nao configurado nem medido pelo codigo.
- Retry: ate 5 tentativas para erros transitórios `408`, `429`, `500`, `503`, `INTERNAL`, `RESOURCE_EXHAUSTED` e `UNAVAILABLE`, além de falhas HTTP, DNS e conexão.
- Para `429`/`RESOURCE_EXHAUSTED`, o gerador tenta respeitar o `retryDelay` informado pela API e acrescenta 2 segundos; sem esse valor, aguarda 40 segundos.
- Para erros de servidor, aguarda 15, 30, 45 e 60 segundos entre tentativas.
- Para falhas de rede, aguarda 5, 10, 15 e 20 segundos entre tentativas.
- Após uma geração bem-sucedida, o gerador espera 10 segundos antes do próximo item do lote.

### Restricoes de arquivos e nomes

- Cartas precisam existir como imagens em `inputs/cartas/` para aparecerem na interface.
- O nome textual da carta precisa corresponder ao nome do arquivo apos remocao de acentos e troca de espacos por `_` para a imagem ser encontrada no render.
- O projeto e nomeado pela data e pelo sufixo definido em `TIPOS[tipo]`; na montagem manual, o nome tambem pode incluir as cartas.
- O audio nao sobrescreve arquivo existente: adiciona contador ao nome.
- O video tambem nao sobrescreve arquivo existente: adiciona contador ao nome.
- Backgrounds selecionados precisam existir; a ausencia de backgrounds impede a receita de video.
- A musica e opcional; se ausente ou invalida, o video pode continuar sem ela.
- Ao finalizar cada receita, `temp/` e limpo.

### Variantes e codigo nao conectado

As personas estao centralizadas em `gerador_gemini.py`; os antigos
`gerador_gemini_bc.py` e `gerador_gemini_half.py` foram removidos.
O `studio/app_2.py` importa `gerador_gemini.py`.

Tambem existem dados em `Postados/` e projetos preexistentes, mas nao ha rotina no fluxo ativo que publique, sincronize ou agende esses arquivos em redes sociais.

### Tipo I Ching

- `ICHING` e um tipo na grade, nao uma fonte selecionada independentemente. A fonte de conhecimento e derivada de `TIPOS["ICHING"]["fonte"]`.
- `inputs/iching_knowledge.json` contém os 64 hexagramas na ordem King Wen, com trigramas, sentido geral, referência genérica de linha mutante e conselho.
- `scripts/gerar_hexagramas.py` gera 64 PNGs 1080x1080 transparentes em `inputs/cartas/`, com linhas na cor `#D4AF37`. O fundo pode ser definido via `--background`.
- No tipo I Ching, o gerador usa as definições e o prompt próprios, mantendo `schema_roteiro` (`id`, `roteiro`, `legenda`) e 6 blocos.
- A REGRA 6 pede registro clássico e enigmático, imagens da natureza e sugestão em português claro, sem termos chineses sem tradução.
- O campo de interface **Linha mutante** indica apenas presença/ausência; é mapeado para `invertida` sem inventar posição de linha.
- Limitação atual: `scripts/video_v2.py` procura imagens `.jpg`; os PNGs são listados e selecionáveis no estúdio, mas ainda não aparecem como imagem da carta no vídeo renderizado.

## Resumo operacional

```text
Streamlit manual
  -> seleciona cartas e contexto
  -> fila de roteiros
  -> Gemini 3.5 Flash + schema JSON
  -> projects/<projeto>/<projeto>.json + legenda.txt
  -> XTTS v2 + referencia WAV
  -> WAV do projeto
  -> escolha/sorteio de BGs e musica
  -> MoviePy/PIL + Whisper
  -> projects/<projeto>/morning_review/<projeto>.mp4
  -> postagem manual fora do repositorio
```
