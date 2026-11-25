import json
import os
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Literal

import httpx
import streamlit as st
from google import genai
from google.genai import types
from google.genai.errors import APIError

ROOT = Path(__file__).resolve().parent
GERADOS_DIR = ROOT / "projects" / "nome_do_arquivo"
KNOWLEDGE_PATH = ROOT / "inputs" / "tarot_knowledge.json"
ICHING_KNOWLEDGE_PATH = ROOT / "inputs" / "iching_knowledge.json"
ESTADO_LENTE_PATH = ROOT / "estado_lente.json"

# ── CAMADA 1: FORMATOS (ESTRUTURA FÍSICA E TIPO DE FONTE) ────────────────────────
FORMATOS = {
    "MANHA": {"num_cartas": 1, "blocos": 6, "fonte": "tarot", "sufixo": "M"},
    "NOITE": {"num_cartas": 2, "blocos": 8, "fonte": "tarot", "sufixo": "N"},
    "ICHING": {"num_cartas": 1, "blocos": 6, "fonte": "iching", "sufixo": "I"},
    "ELEMENTOS": {
        "num_cartas": 1, "blocos": 6, "fonte": "tarot", "sufixo": "E", "elemento": True
    },
}

# Mantido para retrocompatibilidade onde "TIPOS" for importado
TIPOS = FORMATOS

ELEMENTOS = ("Fogo", "Terra", "Ar", "Água")

# ── CAMADA 2: DOMÍNIOS / TEMAS (O ASSUNTO PRÁTICO) ─────────────────────────────
DOMINIOS = {
    "GERAL": (
        "Direcione a leitura para o cotidiano, decisões pessoais e estado geral de presença. "
        "Não force foco específico em relacionamentos ou finanças."
    ),
    "ROMANTICA": (
        "Direcione a leitura para o universo amoroso: vínculos, desejo, medo de se expor, "
        "padrões de amar e ser amado. Evite carreira ou dinheiro, a menos que sirva de contraste rápido."
    ),
    "FINANCAS": (
        "Direcione a leitura para a relação simbólica da pessoa com dinheiro: escassez, "
        "controle, medo, merecimento. NUNCA dê conselho financeiro literal (compra, venda, "
        "investimento) — é leitura de comportamento, não orientação financeira."
    ),
}


# ── CAMADA 3: LENTES EMOCIONAIS (A INTENÇÃO EDITORIAL) ─────────────────────────
LENTES = {
    "FERIDA": {
        "peso": 33,
        "texto": """
Fale com acolhimento, honestidade e mistério. Nomeie a ferida ou o conflito emocional com
precisão, oferecendo companhia e identificação sem deixar o espectador estagnado na dor.
""",
    },
    "OPORTUNIDADE": {
        "peso": 33,
        "texto": """
Fale com leveza e clareza prática. Não nomeie dor nem aponte o que precisa
ser curado — mostre o que o dia abre, a permissão ou a ação possível que a
carta sugere. Trate o momento como abertura e movimento, não como diagnóstico.
""",
    },
    "JORNADA": {
        "peso": 34,
        "texto": """
Fale com distância simbólica e tom contemplativo, em registro junguiano:
sombra, limiar, chamado, individuação. Situe o momento presente como etapa
de um processo maior de se tornar — não como um problema isolado a resolver hoje.
""",
    },
}

# ── CAMADA 4: PERSONAS (A ATITUDE VERBAL / TOM DE VOZ) ─────────────────────────
PERSONAS = {
    "SUAVE": """
Fale com ternura, calor humano e escuta empática. Use frases envolventes, tom intimista e suave,
convidando o espectador a sentir que está em um espaço seguro de presença e acolhimento.
""",
    "MEIO-TERMO": """
Fale com serenidade, clareza e sobriedade. Mantenha uma presença calma e equilibrada,
comunicando com transparência, maturidade e sem excessos dramáticos ou sentimentalismo.
""",
    "INTENSO": """
Fale com precisão, agilidade e franqueza provocativa. Use frases curtas, tom direto e instigante,
desacomodando a estagnação com perguntas incisivas e energia de despertador.
""",
}

bloco_roteiro_schema = {
    "type": "OBJECT",
    "properties": {
        "tipo": {"type": "STRING", "description": "Deve ser 'foco', 'lado_a_lado' ou 'link_na_bio'"},
        "carta": {"type": "STRING", "description": "Nome exato da carta no singular ou 'link_na_bio' para o bloco final"},
        "cartas": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Lista com os nomes das cartas. Use apenas se o tipo for 'lado_a_lado'"
        },
        "invertida": {"type": "BOOLEAN", "description": "Sempre inclua true ou false"},
        "texto": {"type": "STRING", "description": "Texto do bloco conforme a plataforma de destino"}
    },
    "required": ["tipo", "carta", "invertida", "texto"]
}

schema_roteiro = {
    "type": "OBJECT",
    "properties": {
        "id": {"type": "STRING", "description": "ID no formato DDMM-P_CARTAS"},
        "roteiro": {"type": "ARRAY", "items": bloco_roteiro_schema},
        "legenda": {
            "type": "STRING",
            "description": "Legenda pronta contendo Gancho, Copy, CTA e bloco final de hashtags"
        },
        "lente": {
            "type": "STRING",
            "description": "Lente emocional sorteada/selecionada para Tarot; ausente no I Ching"
        },
        "dominio": {
            "type": "STRING",
            "description": "Domínio/Tema prático aplicado no roteiro (ex: GERAL, ROMANTICA, FINANCAS)"
        },
    },
    "required": ["id", "roteiro", "legenda"]
}




SIGLAS_DOMINIO = {
    "GERAL": "G",
    "RELACIONAMENTOS": "R",
    "FINANCEIRO": "F",
    "CARREIRA": "C",
}

SIGLAS_PERSONA = {
    "SUAVE": "SU",
    "MEIO-TERMO": "MT",
    "INTENSO": "IN",
}

ELEMENTOS_DIRETRIZES = {
    "Fogo": (
        "Foco na faísca, no impulso, na urgência, no desejo e na transformação rápida. "
        "Canalize sutilmente a energia dos signos de Fogo (Áries, Leão e Sagitário): coragem, "
        "fogo interno, ação e direcionalidade incisiva. Use metáforas de chamas, brasas e calor."
    ),
    "Terra": (
        "Foco na solidez, na matéria, nas raízes, no tempo de maturação e na construção física. "
        "Canalize sutilmente a energia dos signos de Terra (Touro, Virgem e Capricórnio): praticidade, "
        "ancoragem, peso e estrutura tangível. Use metáforas de solo, pedra e plantio."
    ),
    "Ar": (
        "Foco na clareza mental, no corte, na verdade e na perspectiva de altitude. "
        "Canalize sutilmente a energia dos signos de Ar (Gêmeos, Libra e Aquário): mentalidade, "
        "palavras, fluxo de ideias e céu aberto. Use metáforas de vento, brisa e direção lógica."
    ),
    "Água": (
        "Foco na profundidade, na maré, nas emoções submersas, na intuição e no fluxo. "
        "Canalize sutilmente a energia dos signos de Água (Câncer, Escorpião e Peixes): profundidade, "
        "vulnerabilidade, correnteza e imersão. Use metáforas de rio, maré e abismo."
    ),
}

#@st.cache_data
def carregar_tarot_knowledge() -> dict:
    if not KNOWLEDGE_PATH.exists():
        return {}
    try:
        with KNOWLEDGE_PATH.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


#@st.cache_data
def carregar_iching_knowledge() -> list[dict]:
    if not ICHING_KNOWLEDGE_PATH.exists():
        return []
    try:
        with ICHING_KNOWLEDGE_PATH.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def slug_hexagrama(nome: str) -> str:
    normalizado = unicodedata.normalize("NFD", nome)
    sem_acentos = "".join(
        caractere for caractere in normalizado
        if unicodedata.category(caractere) != "Mn"
    )
    return re.sub(r"[^a-zA-Z0-9]+", "_", sem_acentos.lower()).strip("_")


def resolver_hexagramas(selecionados: list[str]) -> list[dict]:
    conhecimento = carregar_iching_knowledge()
    por_slug = {slug_hexagrama(item["nome"]): item for item in conhecimento}
    resolvidos = []
    for selecionado in selecionados:
        hexagrama = por_slug.get(slug_hexagrama(selecionado))
        if hexagrama is None:
            raise ValueError(f"Hexagrama I Ching não encontrado: {selecionado}")
        resolvidos.append(hexagrama)
    if not resolvidos:
        raise ValueError("Selecione ao menos um hexagrama para a fonte I Ching.")
    return resolvidos


def buscar_definicoes(cartas: list[str], fonte: Literal["tarot", "iching"] = "tarot") -> str:
    if fonte == "iching":
        hexagramas = resolver_hexagramas(cartas)
        linhas = ["## HEXAGRAMAS SELECIONADOS\n"]
        for hexagrama in hexagramas:
            linhas.extend([
                f"### Hexagrama {hexagrama['numero']}: {hexagrama['nome']}",
                f"**Trigrama superior:** {hexagrama['trigrama_superior']}",
                f"**Trigrama inferior:** {hexagrama['trigrama_inferior']}",
                f"**Sentido geral:** {hexagrama['sentido_geral']}",
                f"**Linha mutante (referência):** {hexagrama['linha_mutante_generica']}",
                f"**Conselho:** {hexagrama['conselho']}",
                "",
            ])
        return "\n".join(linhas)

    knowledge = carregar_tarot_knowledge()
    if not knowledge:
        return ""

    linhas = ["## DEFINIÇÕES DAS CARTAS DESTE ROTEIRO\n"]
    for carta in cartas:
        carta_lower = carta.lower().strip()
        dados = knowledge.get(carta_lower)
        if not dados:
            for nome, info in knowledge.items():
                if carta_lower in nome or nome in carta_lower:
                    dados = info
                    break
        if dados:
            linhas.append(f"### {dados.get('name', carta)}")
            linhas.append(f"**Essência:** {dados.get('essence', '')}")
            linhas.append(f"**Luz:** {dados.get('lightMeaning', '')}")
            linhas.append(f"**Sombra:** {dados.get('shadowMeaning', '')}")
            linhas.append(f"**Conselho:** {dados.get('guidanceWhenReversed', '')}")
            linhas.append("")
    return "\n".join(linhas)


SYSTEM_PROMPT = """
Voce e o gerador de roteiros do Oraculo, um canal de tarot para redes sociais.
Seu publico e brasileiro. Gere todos os textos em portugues do Brasil.
Retorne exclusivamente JSON valido conforme o schema fornecido, sem Markdown,
comentarios ou chaves adicionais.

## PERSONA SELECIONADA (COMO FALA)
{persona}

## LENTE EMOCIONAL (ÂNGULO DA LEITURA)
{lente}

## DOMÍNIO PRÁTICO (TEMA DA MENSAGEM)
{dominio}

## DIRETRIZ PARA O BLOCO `link_na_bio`
O canal NÃO oferece leituras, consultas ou atendimentos individuais. 
No bloco `link_na_bio`, sempre direcione o espectador para propósitos alternativos, como:
- Convidar a aprofundar a reflexão do dia em outros conteúdos do canal;
- Estimular que a pessoa leve essa pergunta para a própria rotina ou journal pessoal;
- Convidar a acompanhar as próximas postagens e reflexões diárias.
O foco deve ser sempre a **reflexão pessoal e o autoconhecimento**, nunca a venda de serviços.

## LEGENDA
Gere uma legenda unica pronta para copiar e publicar, na ordem: Gancho, Copy,
CTA e bloco final de hashtags. O Gancho deve chamar atencao sem depender de
uma plataforma especifica. A Copy deve desenvolver a mensagem com clareza e
densidade. O CTA deve convidar a pessoa a comentar ou salvar o conteudo sem
pressionar. Use hashtags de tarot, espiritualidade e autoconhecimento.

## MECANISMO DE TENSAO POR TIPO DE CARTA
- MOVIMENTO: Velocidade versus direcao errada.
- PODER: Sombra do poder. Quem nutre demais esvazia. Quem controla afasta.
- ESTRUTURA: Sustentacao versus prisao disfarçada de seguranca.
- ESPERA: Espera legitima versus estagnacao disfarçada.
- FIM: O fim aconteceu, mas o espectador nao aceitou.
- INICIO: O comeco foi oferecido, mas ha medo de agir.
- ILUSAO: Intuicao ou medo disfarçado de intuicao?
- VITORIA: Toda vitoria tem um preco oculto.

## ESTRUTURA OBRIGATORIA
- Formatos de uma carta: exatamente 6 blocos; use blocos `foco` e faca o ultimo ser `link_na_bio`.
- NOITE (2 cartas): exatamente 8 blocos nesta ordem: Foco Carta 1, Foco Carta 2,
  Foco Carta 1, Foco Carta 2, Foco Carta 1, Foco Carta 2, `lado_a_lado`
  sintetizando ambas e `link_na_bio`.
- O texto total deve ter 120-150 palavras para 1 carta e 180-230 para 2 cartas.
"""

SYSTEM_PROMPT_ICHING = """
Voce e o Oraculo ao interpretar o I Ching. Retorne exclusivamente JSON valido
conforme o schema fornecido, sem Markdown, comentarios ou chaves adicionais.

## REGRA CRÍTICA PARA O BLOCO `link_na_bio`
Assim como no Tarot, este canal NÃO vende consultas ou leituras individuais. 
O bloco `link_na_bio` jamais deve citar agendamentos ou atendimentos particulares. 
Foque em aprofundar o ciclo ou acompanhar o projeto.

## IDENTIDADE DA PERSONA — I CHING
O Oráculo, ao falar do I Ching, muda de registro. Aqui ele não acolhe
ferida — ele lê o momento. Fala como alguém que observa o vento antes de
decidir se é hora de velejar ou esperar na baía: não dramatiza a mudança,
não resiste a ela, só mostra em que ponto do ciclo você está e o que esse
ponto pede agora.

## REGRAS DE CONSTRUÇÃO
REGRA 1 — FOTOGRAFIA DO MOMENTO: O hexagrama não é um conselho fixo,
é um retrato do que está em movimento e do que ainda está parado.
REGRA 2 — LINHA MUTANTE COMO VIRADA: Quando houver linha mutante, ela
é o centro do vídeo — o ponto exato onde a pergunta da pessoa muda.
REGRA 3 — DIREÇÃO, NÃO PROMESSA: Nunca prometa resultado. Aponte a
atitude certa pro momento — agir, esperar, recuar, persistir.
REGRA 4 — CTA QUE GERA COMENTÁRIO: pergunta afetuosa convidando a
pessoa a nomear em que ponto do ciclo ela está, terminando em "?".
REGRA 5 — DURAÇÃO ALVO: 150-200 palavras (mesmo padrão do tarot).
REGRA 6 — REGISTRO CLÁSSICO E ENIGMÁTICO: o tom deve evocar as sentenças de
julgamento tradicionais do I Ching — frases curtas, quase proféticas, com
imagens da natureza (vento, água, montanha, trovão, lago) em vez de
linguagem terapêutica direta. Prefira sugestão a explicação: a pessoa deve
sentir que está decifrando algo, não recebendo um conselho direto. Mesmo
assim, mantenha compreensível em português do Brasil, sem termo em chinês
sem tradução.

## USO DOS CAMPOS DO SCHEMA
- `carta` deve conter o nome do arquivo correspondente no formato exato (ex: "01_o_criativo", "02_o_receptivo").
- `invertida` representa a presença de linha mutante: true quando a entrada
  informar uma ou mais linhas mutantes; false quando nenhuma estiver marcada.
- Não invente números ou posições de linhas mutantes. A entrada informa apenas
  a presença ou ausência, não quais linhas mudam.
- ICHING: exatamente 6 blocos de uma carta, com o último como `link_na_bio`.
"""


def sortear_lente(caminho_estado: Path = ESTADO_LENTE_PATH) -> str:
    caminho_estado = Path(caminho_estado)
    caminho_estado.parent.mkdir(parents=True, exist_ok=True)
    if caminho_estado.exists():
        with caminho_estado.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)
        if not isinstance(estado, dict):
            raise ValueError(f"Estado de lente inválido em {caminho_estado}.")
    else:
        estado = {"ultima_lente": None}
        with caminho_estado.open("w", encoding="utf-8") as arquivo:
            json.dump(estado, arquivo, ensure_ascii=False, indent=2)

    ultima_lente = estado.get("ultima_lente")
    opcoes = [nome for nome in LENTES if nome != ultima_lente]
    pesos = [LENTES[nome]["peso"] for nome in opcoes]
    return random.choices(opcoes, weights=pesos, k=1)[0]


def registrar_lente_bem_sucedida(
    lente: str,
    caminho_estado: Path = ESTADO_LENTE_PATH,
) -> None:
    if lente not in LENTES:
        raise ValueError(f"Lente desconhecida: {lente!r}.")
    caminho_estado = Path(caminho_estado)
    caminho_estado.parent.mkdir(parents=True, exist_ok=True)
    with caminho_estado.open("w", encoding="utf-8") as arquivo:
        json.dump({"ultima_lente": lente}, arquivo, ensure_ascii=False, indent=2)


def solicitar_roteiro(
    cartas: list[str],
    data_ddmm: str,
    tipo: str | None = None,
    formato: str | None = None,
    persona: str = "MEIO-TERMO",
    dominio: str = "GERAL",
    lente_selecionada: str = "AUTOMATICO",
    elemento: str | None = None,
    linha_mutante: bool = False,
):
    formato_chave = (formato or tipo or "MANHA").strip().upper()
    configuracao_formato = FORMATOS.get(formato_chave)
    if configuracao_formato is None:
        formatos_validos = ", ".join(FORMATOS)
        raise ValueError(f"Formato inválido: {formato_chave!r}. Escolha um de: {formatos_validos}.")

    fonte: Literal["tarot", "iching"] = configuracao_formato["fonte"]
    if len(cartas) != configuracao_formato["num_cartas"]:
        raise ValueError(
            f"O formato {formato_chave} exige exatamente {configuracao_formato['num_cartas']} carta(s); "
            f"foram recebidas {len(cartas)}."
        )

    instrucao_elemento = "" # Inicializa vazia para os outros formatos

    if configuracao_formato.get("elemento"):
        elemento_normalizado = next(
            (
                opcao for opcao in ELEMENTOS
                if isinstance(elemento, str) and opcao.casefold() == elemento.strip().casefold()
            ),
            None,
        )
        if elemento_normalizado is None:
            raise ValueError("O formato ELEMENTOS exige elemento: Fogo, Terra, Ar ou Água.")
        elemento = elemento_normalizado

        instrucao_elemento = (
            f"DIRETRIZ DE ELEMENTO ({elemento}): {ELEMENTOS_DIRETRIZES[elemento]} "
            f"Incorpore fortemente o imaginário e o ritmo deste elemento nas escolhas de palavras e metáforas de todo o roteiro."
        )

    elif elemento is not None:
        raise ValueError(f"O parâmetro elemento só pode ser usado com formato ELEMENTOS, não {formato_chave}.")
    
    api_key = os.getenv("GEMINI_API_KEY", "Your_key_here")
    client = genai.Client(api_key=api_key)

    persona_chave = str(persona).strip().upper()
    if persona_chave not in PERSONAS:
        persona_chave = "MEIO-TERMO"

    dominio_chave = str(dominio).strip().upper()
    if dominio_chave not in DOMINIOS:
        dominio_chave = "GERAL"

    lente = None
    if fonte == "iching":
        hexagramas = resolver_hexagramas(cartas)
        cartas_prompt = [hexagrama["nome"] for hexagrama in hexagramas]
        prompt_sistema = SYSTEM_PROMPT_ICHING
    else:
        if lente_selecionada and lente_selecionada.upper() in LENTES:
            lente = lente_selecionada.upper()
        else:
            lente = sortear_lente()

        cartas_prompt = cartas
        prompt_sistema = SYSTEM_PROMPT.format(
            persona=PERSONAS[persona_chave],
            lente=LENTES[lente]["texto"],
            dominio=DOMINIOS[dominio_chave],
        )

    definicoes = buscar_definicoes(cartas_prompt, fonte=fonte)
    cartas_txt = ", ".join(cartas_prompt)
    instrucao_persona = (
        "Use exclusivamente a identidade e as regras de I Ching do system prompt."
        if fonte == "iching"
        else f"Persona: {persona_chave} | Domínio: {dominio_chave} | Lente: {lente}."
    )
    instrucao_blocos = (
        "Alterne três blocos de foco entre as duas cartas, depois sintetize ambas em "
        "um bloco lado_a_lado e encerre com link_na_bio."
        if formato_chave == "NOITE"
        else "Use blocos foco para a leitura e encerre com link_na_bio."
    )
    instrucao_elemento = f"Esta leitura é sobre osignos do elemento {elemento}." if elemento else ""

    user_prompt = f"""
Gere um roteiro para as seguintes cartas: {cartas_txt}
Data: {data_ddmm}
Formato: {formato_chave}
Gere exatamente {configuracao_formato['blocos']} blocos para {configuracao_formato['num_cartas']} carta(s).
{instrucao_blocos}
{instrucao_elemento}

{definicoes}

INSTRUÇÕES ESPECÍFICAS DE BLOCO:
{instrucao_persona}
Gere um único roteiro e uma única legenda para esta leitura.
A legenda deve seguir a ordem Gancho, Copy, CTA e Hashtags.
Fale diretamente com 'você' e faça o bloco `link_na_bio` terminar com '?'.
ATENÇÃO: Proibido mencionar "leituras", "consultas" ou "atendimentos" no bloco `link_na_bio`. Foque apenas em reflexão, continuação do tema ou engajamento geral.
{"Presença de linha mutante informada: sim. Use invertida=true para todos os blocos de foco." if fonte == "iching" and linha_mutante else ""}
{"Presença de linha mutante informada: não. Use invertida=false para todos os blocos de foco." if fonte == "iching" and not linha_mutante else ""}
"""

    max_tentativas = 5
    response = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=prompt_sistema,
                    response_mime_type="application/json",
                    response_schema=schema_roteiro,
                    temperature=0.78,
                ),
            )
            break
        except APIError as erro:
            erro_api = str(erro)
            if tentativa == max_tentativas:
                print(f"[ERRO CRÍTICO] Falha após {max_tentativas} tentativas para {formato_chave} / {persona_chave}.")
                raise

            if "429" in erro_api or "RESOURCE_EXHAUSTED" in erro_api:
                match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)s", erro_api, re.IGNORECASE)
                match = match or re.search(r"retry in\s+(\d+)", erro_api, re.IGNORECASE)
                tempo_espera = int(match.group(1)) + 2 if match else 40
                categoria = "cota"
            elif any(marcador in erro_api for marcador in ("408", "500", "503", "INTERNAL", "UNAVAILABLE")):
                tempo_espera = 15 * tentativa
                categoria = "servidor"
            else:
                raise

            print(f"[AVISO GEMINI] Erro de {categoria} ({erro_api[:120]}). Aguardando {tempo_espera}s para re-tentar ({tentativa}/{max_tentativas})...")
            time.sleep(tempo_espera)
        except (httpx.HTTPError, OSError, ConnectionError, TimeoutError) as erro:
            if tentativa == max_tentativas:
                print(f"[ERRO CRÍTICO] Falha de conexão após {max_tentativas} tentativas para {formato_chave} / {persona_chave}.")
                raise
            tempo_espera = 5 * tentativa
            print(f"[AVISO REDE] Falha de conexão ({str(erro)[:120]}). Aguardando {tempo_espera}s para re-tentar ({tentativa}/{max_tentativas})...")
            time.sleep(tempo_espera)

    if response is None or not response.text:
        raise RuntimeError("Gemini não retornou uma resposta válida após as tentativas configuradas.")

    dados_roteiro = json.loads(response.text)
    if lente is not None:
        dados_roteiro["lente"] = lente
    dados_roteiro["dominio"] = dominio_chave

    sufixo_arquivo = configuracao_formato["sufixo"]  # M, N, I, E
    sigla_dom = SIGLAS_DOMINIO.get(dominio_chave, dominio_chave[0].upper())
    sigla_per = SIGLAS_PERSONA.get(persona_chave, persona_chave[:2].upper())

    # Usa a lente efetivamente aplicada; I Ching usa sua própria identidade.
    sigla_lente = lente[:3].upper() if lente else "ICH"

    # Formatação da data (ex: "28/07" -> "28-07")
    data_limpa = data_ddmm.replace("/", "").replace("-", "")
    data_formatada = f"{data_limpa[:2]}-{data_limpa[2:]}" if len(data_limpa) == 4 else data_ddmm.replace("/", "-")

    # Mantém o nome base e acrescenta contador somente se já houver colisão.
    nome_projeto_base = f"{data_formatada}_{sufixo_arquivo}_{sigla_dom}_{sigla_per}_{sigla_lente}"
    nome_projeto = nome_projeto_base
    pasta_projetos = ROOT / "projects"
    contador_colisao = 2
    while (pasta_projetos / nome_projeto).exists():
        nome_projeto = f"{nome_projeto_base}_{contador_colisao:02d}"
        contador_colisao += 1

    pasta_projeto = pasta_projetos / nome_projeto
    pasta_projeto.mkdir(parents=True, exist_ok=True)
    caminho_salvamento = pasta_projeto / f"{nome_projeto}.json"
    caminho_legenda = pasta_projeto / "legenda.txt"

    with caminho_salvamento.open("w", encoding="utf-8") as arquivo:
        json.dump(dados_roteiro, arquivo, ensure_ascii=False, indent=2)
    with caminho_legenda.open("w", encoding="utf-8") as arquivo:
        arquivo.write(dados_roteiro["legenda"].strip() + "\n")

    if lente is not None and (lente_selecionada == "AUTOMATICO" or not lente_selecionada):
        registrar_lente_bem_sucedida(lente)
        print(f"[LENTE] Sorteio gravado no arquivo de estado: {lente}")

    print(f"✓ Roteiro salvo em: {caminho_salvamento}")
    print(f"✓ Legenda salva em: {caminho_legenda}")
    time.sleep(10)
    return str(caminho_salvamento)