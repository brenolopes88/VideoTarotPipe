import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

if "log_execucao" not in st.session_state:
    st.session_state.log_execucao = []


def registrar_log(mensagem: str, nivel: str = "erro"):
    """Grava uma entrada no log persistente."""
    st.session_state.log_execucao.append({
        "hora": datetime.now().strftime("%H:%M:%S"),
        "nivel": nivel,
        "mensagem": mensagem,
    })


def exibir_log_execucao():
    """Renderiza o log de forma persistente."""
    log = st.session_state.get("log_execucao", [])
    if not log:
        return
    with st.expander(f"📋 Log de execução ({len(log)})", expanded=True):
        for entrada in reversed(log):
            st.markdown(f"`{entrada['hora']}` — {entrada['mensagem']}")
        if st.button("Limpar log"):
            st.session_state.log_execucao = []
            st.rerun()


ROOT = Path(__file__).parent.parent if "scripts" in str(Path(__file__)) or "studio" in str(Path(__file__)) else Path(__file__).parent
sys.path.insert(0, str(ROOT))

PRESET_EXPRESS = "solene"
AUDACITY_PATH = r"C:\Program Files\Audacity\Audacity.exe"
PASTA_CARTAS = ROOT / "inputs" / "cartas"


@st.cache_data
def listar_backgrounds() -> list[str]:
    pasta = ROOT / "inputs" / "videos_fundo"
    if not pasta.exists():
        return []
    return [str(p) for p in sorted(pasta.glob("*.mp4"))]


@st.cache_data
def listar_musicas() -> list[str]:
    pasta = ROOT / "inputs" / "audio_assets"
    if not pasta.exists():
        return []
    return [str(p) for p in sorted(pasta.glob("*.mp3")) + sorted(pasta.glob("*.wav"))]


def listar_vozes(nome_projeto: str = "") -> list[Path]:
    pasta_projetos = ROOT / "projects"
    if not pasta_projetos.exists():
        return []

    if nome_projeto:
        pasta_especifica = pasta_projetos / nome_projeto
        if not pasta_especifica.exists():
            return []
        return sorted(pasta_especifica.glob("*.wav"))

    return sorted(pasta_projetos.rglob("*.wav"))


@st.cache_data
def listar_referencias_voz() -> list[dict]:
    pasta_base = ROOT / "inputs" / "voz"
    if not pasta_base.exists():
        return []

    referencias = []
    for pasta in sorted(pasta_base.iterdir()):
        if not pasta.is_dir():
            continue
        arquivos_wav = sorted(pasta.glob("*.wav"))
        if not arquivos_wav:
            continue

        referencias.append({
            "nome": pasta.name.replace("_", " ").title(),
            "pasta": str(pasta.relative_to(ROOT / "inputs")),
            "temperatura": 0.88,
            "velocidade": 1.12,
            "quantidade": len(arquivos_wav),
        })
    return referencias


from studio.fila_state import (
    init_state, ItemFilaRoteiro, ItemFilaVoz, Receita,
    adicionar_roteiro, adicionar_voz, adicionar_receita,
    remover_roteiro, remover_voz, remover_receita, total_pendente
)
from gerador_gemini import (
    FORMATOS,
    DOMINIOS,
    LENTES,
    PERSONAS,
    ELEMENTOS,
    carregar_iching_knowledge,
    slug_hexagrama,
    solicitar_roteiro,
)
from scripts.voz import gerar_audio_oraculo
from scripts.video_v2 import montar_video_v2, extrair_thumbnail
from scripts.utils import limpar_pasta_temp, obter_cartas_disponiveis

# ── Configuração Streamlit ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Oráculo Studio",
    layout="wide",
    initial_sidebar_state="collapsed"
)


@st.cache_data
def obter_thumbnail_cached(bg_path: str):
    return extrair_thumbnail(bg_path)


init_state()

DIRS = {
    "projetos": ROOT / "projects",
}
for d in DIRS.values():
    d.mkdir(parents=True, exist_ok=True)


def listar_jsons(pasta: Path) -> list[Path]:
    return sorted(pasta.rglob("*.json"))


def carregar_roteiro(path: Path) -> list | None:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
        with open(path, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, list) else [dados]
    except Exception:
        return None


def salvar_roteiro(path: Path, dados):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def extrair_cartas_roteiro(roteiro: dict) -> list[str]:
    cartas = []
    for bloco in roteiro.get("roteiro", []):
        carta = bloco.get("carta")
        if carta and isinstance(carta, str) and carta not in ("link_na_bio",):
            cartas.append(carta)
        elif carta and isinstance(carta, list):
            cartas.extend([c for c in carta if c not in ("link_na_bio",)])
        cartas_lista = bloco.get("cartas")
        if cartas_lista and isinstance(cartas_lista, list):
            cartas.extend([c for c in cartas_lista if isinstance(c, str)])
    vistas = set()
    resultado = []
    for c in cartas:
        if isinstance(c, str) and c not in vistas:
            vistas.add(c)
            resultado.append(c)
    return resultado


def gerar_nome_projeto() -> str:
    roteiro_path = st.session_state.get("roteiro_selecionado")
    if roteiro_path:
        return Path(roteiro_path).stem
    return "projeto_sem_nome"


def log(msg: str):
    registrar_log(msg, "info")


if "roteiros_selecionados" not in st.session_state:
    st.session_state.roteiros_selecionados = []
if "thumbs_cache" not in st.session_state:
    st.session_state.thumbs_cache = {}
if "bgs_marcados" not in st.session_state:
    st.session_state.bgs_marcados = []
if "bgs_selecionados" not in st.session_state:
    st.session_state.bgs_selecionados = []

# ── Sidebar — Fila Unificada + Execução ───────────────────────────────────────
with st.sidebar:
    st.title("🔮 Fila de Produção")
    st.divider()

    exibir_log_execucao()

    total = total_pendente()
    if total == 0:
        st.info("Nenhum item na fila.")
    else:
        st.success(f"{total} item(s) na fila.")
        if st.button(
            "🚀 Iniciar produção",
            width='stretch',
            disabled=st.session_state.executando,
            type="primary"
        ):
            st.session_state.executando = True
            st.session_state.log_execucao = []
            st.rerun()

    if st.session_state.executando:
        st.warning("⚙️ Produção em andamento...")

    tem_itens = (
        st.session_state.fila_roteiros or
        st.session_state.fila_vozes or
        st.session_state.fila_receitas
    )
    if tem_itens:
        st.divider()
        if st.session_state.fila_roteiros:
            st.caption("**📝 Roteiros na Fila**")
            for i, item in enumerate(st.session_state.fila_roteiros):
                col_i, col_d = st.columns([4, 1])
                fmt = item.formato or item.tipo or "MANHA"
                fonte = FORMATOS.get(fmt, {}).get("fonte", "tarot").upper()
                mutante = " • mutante" if fmt == "ICHING" and item.linha_mutante else ""
                elemento = f" • {item.elemento}" if fmt == "ELEMENTOS" and item.elemento else ""
                lente_lbl = f" | {item.lente}" if item.lente and item.lente != "AUTOMATICO" else ""
                
                col_i.caption(f"**{fmt}** ({fonte}) • {item.dominio}{lente_lbl}\n*Voz:* {item.persona}{elemento} — {item.cartas}{mutante}")
                if col_d.button("✕", key=f"sid_rot_{i}"):
                    remover_roteiro(i)
                    st.rerun()

        if st.session_state.fila_vozes:
            st.caption("**🎙️ Vozes na Fila**")
            for i, item in enumerate(st.session_state.fila_vozes):
                col_i, col_d = st.columns([4, 1])
                col_i.caption(f"{item.nome_projeto} — {item.preset}")
                if col_d.button("✕", key=f"sid_voz_{i}"):
                    remover_voz(i)
                    st.rerun()

        if st.session_state.fila_receitas:
            st.caption("**🎬 Receitas na Fila**")
            for i, receita in enumerate(st.session_state.fila_receitas):
                col_i, col_d = st.columns([4, 1])
                col_i.caption(receita.nome_projeto)
                if col_d.button("✕", key=f"sid_rec_{i}"):
                    remover_receita(i)
                    st.rerun()

# ── Execução das Filas ────────────────────────────────────────────────────────
if st.session_state.executando:
    placeholder = st.empty()
    with placeholder.container():
        st.subheader("⚙️ Processando Fila...")
        progresso = st.progress(0)
        status = st.empty()
        total_itens = total_pendente()
        concluidos = 0

        for item in list(st.session_state.fila_roteiros):
            fmt = item.formato or item.tipo or "MANHA"
            fonte = FORMATOS.get(fmt, {}).get("fonte", "tarot").upper()
            status.info(f"📝 Gerando roteiro {fmt}: {item.cartas}")
            log(f"[ROTEIRO {fmt} / {fonte}] {item.cartas} — Domínio: {item.dominio} | Voz: {item.persona}")
            try:
                cartas_list = [c.strip() for c in item.cartas.split(",") if c.strip()]
                
                caminho_salvo = solicitar_roteiro(
                    cartas=cartas_list,
                    data_ddmm=item.data,
                    formato=fmt,
                    persona=item.persona,
                    dominio=item.dominio,
                    lente_selecionada=item.lente,
                    elemento=item.elemento,
                    linha_mutante=item.linha_mutante,
                )
                dados_gerados = carregar_roteiro(Path(caminho_salvo))
                lente_usada = dados_gerados[0].get("lente") if dados_gerados else None
                if lente_usada:
                    log(f"[LENTE] {lente_usada} — {caminho_salvo}")
                log(f"✓ Roteiro salvo em: {caminho_salvo}")
                
                sufixo = FORMATOS.get(fmt, {}).get("sufixo", "")
                msg_ok = f"  ✓ Roteiro {item.data}-{sufixo} salvo em: {caminho_salvo}"
                log(msg_ok)
                resumo_lente = f" / lente {lente_usada}" if lente_usada else ""
                st.toast(f"📝 Roteiro gerado: {fmt} / {item.persona}{resumo_lente}", icon="✅")

            except Exception as e:
                msg_err = f"  ✗ Erro em {fmt} ({item.persona}): {e}"
                log(msg_err)
                registrar_log(f"Falha ao gerar roteiro: {e}", "erro")
                st.error(f"Erro ao salvar roteiro ({fmt}): {e}")
                
            st.session_state.fila_roteiros.remove(item)
            concluidos += 1
            progresso.progress(concluidos / max(total_itens, 1))

        for item in list(st.session_state.fila_vozes):
            status.info(f"🎙️ Gerando voz: {item.nome_projeto}")
            log(f"[VOZ] {item.nome_projeto} — preset: {item.preset}")
            try:
                dados = carregar_roteiro(Path(item.roteiro_path))
                if dados:
                    textos = [b["texto"] for b in dados[0].get("roteiro", [])]
                    sufixo = f"_{item.preset.lower()}"
                    gerar_audio_oraculo(
                        textos, item.nome_projeto,
                        temperature=item.temperatura,
                        speed=item.velocidade,
                        sufixo=sufixo,
                        pasta_referencia=item.referencia_voz
                    )
                    log(f"  ✓ Voz criada: {item.nome_projeto}{sufixo}")
            except Exception as e:
                log(f"  ✗ Erro voz: {e}")
            st.session_state.fila_vozes.remove(item)
            concluidos += 1
            progresso.progress(concluidos / max(total_itens, 1))

        for receita in list(st.session_state.fila_receitas):
            status.info(f"🎬 Renderizando vídeo: {receita.nome_projeto}")
            log(f"[VÍDEO] {receita.nome_projeto}")
            try:
                dados = carregar_roteiro(Path(receita.roteiro_path))
                if dados:
                    pasta_saida = str(DIRS["projetos"] / receita.nome_projeto)
                    os.makedirs(pasta_saida, exist_ok=True)

                    montar_video_v2(
                        audio_path=receita.voz_path,
                        roteiro_cenas=dados[0].get("roteiro", []),
                        pasta_saida=pasta_saida,
                        nome_projeto=receita.nome_projeto,
                        backgrounds_por_bloco=receita.backgrounds_por_bloco,
                        musica_path=receita.musica_path,
                        musica_volume=receita.musica_volume,
                    )
                    log(f"  ✓ Render concluído em {pasta_saida}")
            except Exception as e:
                log(f"  ✗ Erro render: {e}")
            finally:
                limpar_pasta_temp()
            st.session_state.fila_receitas.remove(receita)
            concluidos += 1
            progresso.progress(concluidos / max(total_itens, 1))

        status.success("✅ Processamento da fila concluído!")
        st.session_state.executando = False
        time.sleep(1.5)
        st.rerun()

# ── Abas Principais ──────────────────────────────────────────────────────────
tab_gerador, tab_estudio, tab_bgs = st.tabs([
    "🔮 1. Gerador em Lote",
    "🎙️ 2. Estúdio & Vozes",
    "🎬 3. BGs & Render"
])

# ══════════════════════════════════════════════════════════════════════════════
# ABA 1 — GERADOR EM LOTE (GEMINI 3.5 FLASH)
# ══════════════════════════════════════════════════════════════════════════════
with tab_gerador:
    st.subheader("🔮 Gerador de Roteiros em Lote (Google AI Studio)")
    st.caption("Preencha a grade da semana para disparar a geração massiva com 'gemini-3.5-flash'")

    conhecimento_iching = carregar_iching_knowledge()
    cartas_iching = [slug_hexagrama(item["nome"]) for item in conhecimento_iching]
    conjunto_iching = set(cartas_iching)
    cartas_tarot = [
        carta for carta in obter_cartas_disponiveis(PASTA_CARTAS)
        if carta not in conjunto_iching
    ]
    cartas_disponiveis = sorted(set(cartas_tarot + cartas_iching))

    formatos_list = list(FORMATOS)
    dominios_list = list(DOMINIOS)
    lentes_opcoes = ["AUTOMATICO"] + list(LENTES)
    personas_list = list(PERSONAS)

    nomes_formatos = {
        "MANHA": "MANHÃ (1 Carta)",
        "NOITE": "NOITE (2 Cartas)",
        "ICHING": "I CHING (Hexagrama)",
        "ELEMENTOS": "ELEMENTOS (1 Carta)",
    }

    dados_limpos = [
        {
            "Data (DD/MM)": "",
            "Formato": "MANHA",
            "Domínio": "GERAL",
            "Lente Emocional": "AUTOMATICO",
            "Persona": "MEIO-TERMO",
            "Elemento": None,
            "Cartas": [],
            "Linha mutante": False,
        }
    ]

    df_padrao = pd.DataFrame(dados_limpos)

    df_editado = st.data_editor(
        df_padrao,
        num_rows="dynamic",
        width='stretch',
        column_config={
            "Data (DD/MM)": st.column_config.TextColumn(
                "Data (DD/MM)",
                help="Digite a data no formato DD/MM (ex: 28/07)",
                max_chars=5,
                required=False
            ),
            "Formato": st.column_config.SelectboxColumn(
                "Formato",
                options=formatos_list,
                format_func=lambda v: nomes_formatos.get(v, v),
                required=True,
            ),
            "Domínio": st.column_config.SelectboxColumn(
                "Domínio",
                options=dominios_list,
                required=True,
            ),
            "Lente Emocional": st.column_config.SelectboxColumn(
                "Lente Emocional",
                options=lentes_opcoes,
                help="AUTOMATICO sorteia mantendo a variação no feed, ou escolha uma lente fixa",
                required=True,
            ),
            "Persona": st.column_config.SelectboxColumn(
                "Persona (Voz)",
                options=personas_list,
                required=True
            ),
            "Elemento": st.column_config.SelectboxColumn(
                "Elemento (p/ ELEMENTOS)",
                options=ELEMENTOS,
                default=None,
                required=False,
            ),
            "Cartas": st.column_config.MultiselectColumn(
                "Carta(s)",
                options=cartas_disponiveis,
                help="Selecione cartas Tarot ou hexagramas conforme o formato",
                required=False
            ),
            "Linha mutante": st.column_config.CheckboxColumn(
                "Linha mutante",
                help="No I Ching, marque se a leitura possui linha mutante.",
                default=False,
            ),
        }
    )

    col_g1, col_g2 = st.columns([2, 1])
    if col_g1.button("➕ Adicionar Lote à Fila de Geração", type="primary", width='stretch'):
        adicionados = 0
        for _, row in df_editado.iterrows():
            cartas_val = row["Cartas"]
            if isinstance(cartas_val, list):
                cartas_str = ", ".join(cartas_val)
            else:
                cartas_str = str(cartas_val)

            if row["Data (DD/MM)"] and cartas_str:
                formato = str(row["Formato"])
                configuracao_formato = FORMATOS.get(formato)
                if configuracao_formato is None:
                    st.error(f"Formato inválido: {formato}. A linha não foi adicionada.")
                    continue

                cartas_linha = [carta.strip() for carta in cartas_str.split(",") if carta.strip()]
                esperadas = configuracao_formato["num_cartas"]
                if len(cartas_linha) != esperadas:
                    st.error(
                        f"{nomes_formatos.get(formato, formato)} exige {esperadas} carta(s); "
                        f"a linha tem {len(cartas_linha)}."
                    )
                    continue

                fonte = configuracao_formato["fonte"]
                permitidas = set(cartas_iching if fonte == "iching" else cartas_tarot)
                invalidas = [carta for carta in cartas_linha if carta not in permitidas]
                if invalidas:
                    st.error(
                        f"O formato {nomes_formatos.get(formato, formato)} não aceita estas seleções: "
                        f"{', '.join(invalidas)}. A linha não foi adicionada."
                    )
                    continue

                elemento_valor = row["Elemento"]
                elemento = None if pd.isna(elemento_valor) else str(elemento_valor).strip()
                if configuracao_formato.get("elemento") and elemento not in ELEMENTOS:
                    st.error("Selecione Fogo, Terra, Ar ou Água para o formato ELEMENTOS.")
                    continue

                adicionar_roteiro(ItemFilaRoteiro(
                    cartas=cartas_str,
                    persona=str(row["Persona"]),
                    formato=formato,
                    tipo=formato,
                    dominio=str(row["Domínio"]),
                    lente=str(row["Lente Emocional"]),
                    elemento=elemento if configuracao_formato.get("elemento") else None,
                    linha_mutante=bool(row["Linha mutante"]),
                    modelo="gemini-3.5-flash",
                    data=str(row["Data (DD/MM)"])
                ))
                adicionados += 1
        st.success(f"{adicionados} roteiros adicionados à Fila na Sidebar! Clique em 'Iniciar Produção' lá para disparar.")
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ABA 2 — ESTÚDIO DE ROTEIROS & ÁUDIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_estudio:
    col_rot, col_voz = st.columns([1.3, 1.7])

    with col_rot:
        st.subheader("📝 Roteiros Gerados")
        jsons = listar_jsons(DIRS["projetos"])

        if not jsons:
            st.info("Nenhum roteiro em 'projects/'")
        else:
            rows = []
            for p in jsons:
                dados = carregar_roteiro(p)
                if dados:
                    cartas_rot = extrair_cartas_roteiro(dados[0])
                    rows.append({
                        "path": p,
                        "nome": p.stem,
                        "cartas": ", ".join(cartas_rot) if cartas_rot else "—",
                        "blocos": len(dados[0].get("roteiro", [])),
                        "lente": dados[0].get("lente") or (
                            "I Ching (prompt próprio)" if p.stem.endswith("-I") else "Não registrada"
                        ),
                    })

            ch0, ch1, ch2, ch3 = st.columns([.35, 1.7, 2.2, 1.2])
            ch0.caption("**✓**")
            ch1.caption("**Nome**")
            ch2.caption("**Cartas**")
            ch3.caption("**Lente**")

            for row in rows:
                c0, c1, c2, c3 = st.columns([.35, 1.7, 2.2, 1.2])
                marcado = str(row["path"]) in st.session_state.roteiros_selecionados
                ativo = st.session_state.roteiro_selecionado == str(row["path"])

                if c0.checkbox(
                    label=f"Selecionar {row['nome']}", 
                    value=marcado, 
                    key=f"chk_rot_{row['nome']}",
                    label_visibility="collapsed"
                ):
                    if str(row["path"]) not in st.session_state.roteiros_selecionados:
                        st.session_state.roteiros_selecionados.append(str(row["path"]))
                else:
                    if str(row["path"]) in st.session_state.roteiros_selecionados:
                        st.session_state.roteiros_selecionados.remove(str(row["path"]))

                nome_label = f"**{row['nome']}**" if ativo else row["nome"]
                c1.caption(nome_label)
                c2.caption(row["cartas"])
                c3.caption(row["lente"])

            st.divider()
            n_sel = len(st.session_state.roteiros_selecionados)

            referencias_voz = listar_referencias_voz()
            opcoes_referencia = {
                f"{ref['nome']} ({ref['quantidade']} WAVs)": ref
                for ref in referencias_voz
            }
            if opcoes_referencia:
                referencia_label = st.selectbox(
                    "Banco de voz de referência",
                    options=list(opcoes_referencia),
                    key="referencia_voz_selecionada",
                )
                referencia_escolhida = opcoes_referencia[referencia_label]
                st.caption(
                    f"Preset: temperatura {referencia_escolhida['temperatura']:.2f} | "
                    f"velocidade {referencia_escolhida['velocidade']:.2f}"
                )
            else:
                referencia_escolhida = None
                st.warning("Nenhum banco de voz com arquivos WAV foi encontrado em inputs/voz/.")

            ca, cb = st.columns(2)
            if ca.button("✅ Ativar Selecionado", width='stretch', disabled=n_sel != 1):
                path_sel = Path(st.session_state.roteiros_selecionados[0])
                st.session_state.roteiro_selecionado = str(path_sel)
                st.session_state.roteiro_dados = carregar_roteiro(path_sel)
                st.success(f"'{path_sel.stem}' ativo.")
                st.rerun()

            if cb.button(
                f"🎙️ Gerar Vozes ({n_sel})",
                type="primary",
                width='stretch',
                disabled=n_sel == 0 or referencia_escolhida is None,
            ):
                for path_str in st.session_state.roteiros_selecionados:
                    nome_proj = Path(path_str).stem
                    adicionar_voz(ItemFilaVoz(
                        roteiro_path=path_str,
                        nome_projeto=nome_proj,
                        referencia_voz=referencia_escolhida["pasta"],
                        preset=referencia_escolhida["nome"],
                        temperatura=referencia_escolhida["temperatura"],
                        velocidade=referencia_escolhida["velocidade"],
                        n_geracoes=1
                    ))
                st.success(f"{n_sel} voz(es) adicionada(s) à fila!")
                st.session_state.roteiros_selecionados = []
                st.rerun()

            with st.expander("✏️ Ver/Editar JSON Ativo", expanded=False):
                if st.session_state.roteiro_selecionado:
                    p_ativo = Path(st.session_state.roteiro_selecionado)
                    dados_edit = carregar_roteiro(p_ativo)
                    if dados_edit:
                        txt = st.text_area("JSON", value=json.dumps(dados_edit, ensure_ascii=False, indent=2), height=250)
                        if st.button("💾 Salvar Alterações"):
                            try:
                                salvar_roteiro(p_ativo, json.loads(txt))
                                st.success("Salvo!")
                            except Exception as e:
                                registrar_log(f"Falha ao salvar roteiro: {e}", "erro")
                                st.error(f"Erro no JSON: {e}")

    with col_voz:
        st.subheader("🎙️ Gerenciador de Vozes")
        if not st.session_state.roteiro_selecionado:
            st.info("Selecione e ative um roteiro à esquerda para ver suas vozes.")
        else:
            nome_base = Path(st.session_state.roteiro_selecionado).stem
            st.caption(f"Roteiro Ativo: **{nome_base}**")
            wavs = listar_vozes(nome_base)

            if not wavs:
                st.info("Nenhuma voz gerada para este roteiro ainda.")
            else:
                for wav in wavs:
                    with st.container(border=True):
                        st.caption(f"🔊 **{wav.name}**")
                        st.audio(str(wav))
                        v_col1, v_col2, v_col3, v_col4 = st.columns([1, 1, 1.2, 1])

                        if v_col1.button("✅ Usar", key=f"u_{wav.stem}", width='stretch'):
                            st.session_state.voz_selecionada = str(wav)
                            st.success("Voz selecionada para render!")

                        if v_col2.button("🚀 Express", key=f"exp_{wav.stem}", width='stretch', help="Sorteia BGs e música a 0.12 e envia direto para a fila!"):
                            st.session_state.voz_selecionada = str(wav)
                            
                            roteiro_dados = st.session_state.roteiro_dados
                            n_blocos = len(roteiro_dados[0].get("roteiro", [])) if roteiro_dados else 6
                            
                            all_bgs = listar_backgrounds()
                            if all_bgs:
                                bgs_sorteados = random.sample(all_bgs, min(n_blocos, len(all_bgs)))
                                bgs_finais = [bgs_sorteados[i % len(bgs_sorteados)] for i in range(n_blocos)]
                            else:
                                bgs_finais = []
                            
                            musicas = listar_musicas()
                            musica_sorteada = random.choice(musicas) if musicas else None
                            
                            if bgs_finais:
                                adicionar_receita(Receita(
                                    nome_projeto=nome_base,
                                    roteiro_path=st.session_state.roteiro_selecionado,
                                    voz_path=str(wav),
                                    backgrounds_por_bloco=bgs_finais,
                                    musica_path=musica_sorteada,
                                    musica_volume=0.12
                                ))
                                st.success(f"🚀 Vídeo Express enviado para a fila! (Vol Mús: 0.12 | {len(bgs_finais)} BGs)")
                                st.rerun()
                            else:
                                registrar_log("Falha no Express: nenhum BG encontrado em 'inputs/videos_fundo/'.", "erro")
                                st.error("Nenhum BG encontrado em 'inputs/videos_fundo/'")

                        if v_col3.button("🎵 Audacity", key=f"aud_{wav.stem}", width='stretch'):
                            import subprocess
                            subprocess.Popen([AUDACITY_PATH, str(wav)])

                        if v_col4.button("🗑️ Apagar", key=f"del_{wav.stem}", width='stretch'):
                            os.remove(str(wav))
                            st.rerun()

            if st.session_state.voz_selecionada:
                st.success(f"Voz ativa para Vídeo: **{Path(st.session_state.voz_selecionada).name}**")

# ══════════════════════════════════════════════════════════════════════════════
# ABA 3 — BACKGROUNDS VISUAIS & MONTAGEM DO VÍDEO
# ══════════════════════════════════════════════════════════════════════════════
with tab_bgs:
    st.subheader("🎬 Direção de Arte (Backgrounds) & Render")
    bgs = listar_backgrounds()

    if not bgs:
        st.warning("Nenhum arquivo MP4 em 'inputs/videos_fundo/'")
    else:
        roteiro_dados = st.session_state.roteiro_dados
        n_blocos = len(roteiro_dados[0].get("roteiro", [])) if roteiro_dados else 6

        if st.session_state.bgs_marcados:
            sel = st.session_state.bgs_marcados
            st.session_state.bgs_selecionados = [sel[i % len(sel)] for i in range(n_blocos)]
        else:
            st.session_state.bgs_selecionados = []

        st.caption(f"Roteiro atual precisa de **{n_blocos} BGs**. Selecionados: **{len(st.session_state.bgs_marcados)}/{n_blocos}**")

        b_ctrl1, b_ctrl2 = st.columns([1, 4])
        if b_ctrl1.button("🎲 Seleção Aleatória", width='stretch'):
            if bgs:
                bgs_shuffled = random.sample(bgs, len(bgs))
                st.session_state.bgs_marcados = bgs_shuffled[:min(n_blocos, len(bgs_shuffled))]
                
                for bg_item in bgs:
                    nome_item = Path(bg_item).stem
                    key_item = f"bg_tab3_{nome_item}"
                    st.session_state[key_item] = (bg_item in st.session_state.bgs_marcados)

                st.rerun()

        for bg in bgs:
            if bg not in st.session_state.thumbs_cache:
                st.session_state.thumbs_cache[bg] = extrair_thumbnail(bg)

        linhas = [bgs[i:i+4] for i in range(0, len(bgs), 4)]
        with st.container(height=450):
            for linha in linhas:
                cols = st.columns(4)
                for j, bg_path in enumerate(linha):
                    with cols[j]:
                        thumb = st.session_state.thumbs_cache.get(bg_path)
                        if thumb is not None:
                            st.image(thumb, width='stretch')
                        
                        nome = Path(bg_path).stem
                        key_chk = f"bg_tab3_{nome}"
                        
                        if key_chk not in st.session_state:
                            st.session_state[key_chk] = bg_path in st.session_state.bgs_marcados

                        marcado = st.checkbox(f"#{nome[:12]}", key=key_chk)

                        if marcado and bg_path not in st.session_state.bgs_marcados:
                            if len(st.session_state.bgs_marcados) < n_blocos:
                                st.session_state.bgs_marcados.append(bg_path)
                            else:
                                st.warning(f"Limite de {n_blocos} BGs atingido.")
                                st.session_state[key_chk] = False
                        elif not marcado and bg_path in st.session_state.bgs_marcados:
                            st.session_state.bgs_marcados.remove(bg_path)

        st.divider()

        st.markdown("### 🍳 Receita do Vídeo Final")
        r_col2, r_col3 = st.columns(2)

        with r_col2:
            musicas = listar_musicas()
            musica_sel = st.selectbox("Música de Fundo (Opcional)", options=["Sem música"] + musicas)
            vol_musica = st.slider("Volume Música", 0.0, 0.5, 0.15)

        with r_col3:
            st.write("")
            st.write("")
            
            pode_render = (
                bool(st.session_state.get("roteiro_selecionado")) and 
                bool(st.session_state.get("voz_selecionada")) and 
                len(st.session_state.bgs_selecionados) > 0
            )

            if st.button("➕ Adicionar à Fila Render", type="primary", width='stretch', disabled=not pode_render):
                nome_proj = gerar_nome_projeto()
                adicionar_receita(Receita(
                    nome_projeto=nome_proj,
                    roteiro_path=st.session_state.roteiro_selecionado,
                    voz_path=st.session_state.voz_selecionada,
                    backgrounds_por_bloco=list(st.session_state.bgs_selecionados),
                    musica_path=musica_sel if musica_sel != "Sem música" else None,
                    musica_volume=vol_musica
                ))
                st.success(f"Receita '{nome_proj}' adicionada à fila!")
                st.rerun()

            if not pode_render:
                faltantes = []
                if not st.session_state.get("roteiro_selecionado"): faltantes.append("Roteiro")
                if not st.session_state.get("voz_selecionada"): faltantes.append("Voz")
                if len(st.session_state.bgs_selecionados) == 0: faltantes.append("BGs")
                st.caption(f"⚠️ Selecione: {', '.join(faltantes)}")