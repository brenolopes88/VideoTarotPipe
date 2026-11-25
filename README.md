# Oráculo — Pipeline de Roteiro, Voz e Vídeo

Aplicação local em **Python** e **Streamlit** para geração de roteiros, síntese de voz e renderização de vídeos verticais curtos (`1080x1920`) com foco em Tarot e I Ching[cite: 8, 9, 16].

## Visão Geral e Arquitetura

O fluxo principal é orquestrado localmente via Streamlit em `studio/app_2.py`, com estado de filas em `studio/fila_state.py` (`st.session_state`)[cite: 8]:

- **Geração de roteiros (`gerador_gemini.py`):** integra com a API do Google Gemini (`google-genai`) retornando JSON estruturado (`id`, `roteiro`, `legenda` e `lente` opcional em Tarot) a partir das bases em `inputs/tarot_knowledge.json` ou `inputs/iching_knowledge.json`[cite: 8].
- **Síntese de voz (`scripts/voz.py` e `scripts/audio_fx.py`):** gera áudio `.wav` com Coqui TTS / XTTS v2 (`tts_models/multilingual/multi-dataset/xtts_v2`), usando bancos de referência em `inputs/voz/`, segmentação/pausas em `scripts/utils.py` e parâmetros de temperatura e velocidade[cite: 8, 9].
- **Montagem e renderização de vídeo (`scripts/video_v2.py`):** compõe cenas verticais `1080x1920` (30 FPS, H.264/AAC) com fundos de `inputs/videos_fundo/`, cartas de `inputs/cartas/`, trilha opcional de `inputs/audio_assets/`, legendas queimadas via Whisper (`small`) e limpeza da pasta `temp/` ao final de cada receita[cite: 8].

> **Nota sobre o workspace:** O ecossistema da operação também contempla módulos independentes para carrosséis (`oraculo_carrossel/`) e acompanhamento de experimentos (`Tracker/oraculo_tracker/`), sem compartilhamento direto de ambientes ou bancos entre eles[cite: 16].

## Tipos Suportados e Filas

Na grade de geração em lote (`studio/app_2.py`), os tipos disponíveis incluem[cite: 8]:
- **Tarot:** `MANHA` (1 carta, 6 blocos), `NOITE` (2 cartas, 8 blocos), `ROMANTICA` (1 carta, 6 blocos), `FINANCAS` (1 carta, 6 blocos) e `ELEMENTOS` (1 carta, 6 blocos, exigindo elemento `Fogo`, `Terra`, `Ar` ou `Água`)[cite: 8].
- **I Ching:** `ICHING` (1 hexagrama de `inputs/iching_knowledge.json`, 6 blocos, com suporte a flag de linha mutante)[cite: 8].

O processamento ocorre em três filas sequenciais ao iniciar a produção:
1. `fila_roteiros` -> salva `<projeto>.json` e `legenda.txt` em `projects/<DD-MM-{sufixo}>/`[cite: 8].
2. `fila_vozes` -> gera o `.wav` na pasta do projeto a partir do roteiro e do banco de voz selecionado[cite: 8].
3. `fila_receitas` -> monta e exporta o `.mp4` em `projects/<nome_projeto>/morning_review/` (com incrementos `_01`, `_02` se já existir)[cite: 8].

## Como Executar

Com o ambiente virtual do projeto ativo e `GEMINI_API_KEY` configurada no ambiente[cite: 8, 16]:

```powershell
python -m streamlit run studio/app_2.py
```[cite: 16]

## Limitações Atuais e Privacidade

- A publicação e o agendamento nas redes sociais não fazem parte do fluxo automatizado do repositório (são feitos manualmente após a exportação)[cite: 8].
- Em `ICHING`, a renderização de vídeo (`scripts/video_v2.py`) busca imagens `.jpg`, enquanto os hexagramas gerados por `scripts/gerar_hexagramas.py` são `.png`[cite: 8].
- **Não versione** chaves de API, áudios/vídeos gerados em `projects/`, pastas temporárias ou arquivos locais de estado/cache no GitHub[cite: 8].