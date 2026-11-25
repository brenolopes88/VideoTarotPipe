import json
import logging
import os
import random
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
#import httpx
import sqlite3


CAMINHO_DB = r"C:\TarotStudio\Tracker\oraculo_tracker.db"
ROOT = Path(__file__).resolve().parent
LOTE_PATH = ROOT / "lote_agendado.json"
LOG_PATH = ROOT / "log_noturno.txt"
INTERVALO_GEMINI_SEGUNDOS = 10
VARIACOES_VOZ = (
    {"nome": "v1", "temperature": 0.85, "speed": 1.05},
    {"nome": "v2", "temperature": 0.88, "speed": 1.12},
    {"nome": "v3", "temperature": 0.92, "speed": 1.10},
)

logger = logging.getLogger("oraculo_lote_noturno")
_ultima_chamada_gemini: float | None = None


def configurar_log() -> None:
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formato = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    arquivo = logging.FileHandler(LOG_PATH, encoding="utf-8")
    arquivo.setFormatter(formato)
    console = logging.StreamHandler()
    console.setFormatter(formato)
    logger.addHandler(arquivo)
    logger.addHandler(console)
    logger.propagate = False


def carregar_lote(caminho: Path = LOTE_PATH) -> list[dict[str, Any]]:
    if not caminho.exists():
        raise FileNotFoundError(
            f"Arquivo de lote não encontrado: {caminho}. Crie lote_agendado.json a partir do exemplo."
        )
    with caminho.open("r", encoding="utf-8-sig") as arquivo:
        itens = json.load(arquivo)
    if not isinstance(itens, list):
        raise ValueError("A raiz de lote_agendado.json deve ser uma lista JSON.")
    return itens


def salvar_lote_pendente(itens: list[dict[str, Any]], caminho: Path = LOTE_PATH) -> None:
    """Reescreve o arquivo JSON mantendo apenas os itens que ainda faltam rodar."""
    with caminho.open("w", encoding="utf-8") as arquivo:
        json.dump(itens, arquivo, ensure_ascii=False, indent=2)


def validar_item(item: Any, indice: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"Item {indice}: cada tarefa deve ser um objeto JSON.")

    obrigatorios = ("data", "formato", "cartas", "banco_voz")
    faltantes = [campo for campo in obrigatorios if campo not in item]
    if faltantes:
        raise ValueError(f"Item {indice}: campos obrigatórios ausentes: {', '.join(faltantes)}.")

    if not isinstance(item["cartas"], list) or not item["cartas"]:
        raise ValueError(f"Item {indice}: 'cartas' precisa ser uma lista não vazia.")
    if not all(isinstance(carta, str) and carta.strip() for carta in item["cartas"]):
        raise ValueError(f"Item {indice}: cada carta deve ser uma string não vazia.")

    data = str(item["data"]).strip()
    formato = str(item["formato"]).strip().upper()
    banco_voz = str(item["banco_voz"]).strip()
    if not data or not formato or not banco_voz:
        raise ValueError(f"Item {indice}: data, formato e banco_voz não podem estar vazios.")

    raiz_inputs = (ROOT / "inputs").resolve()
    pasta_referencia = (raiz_inputs / banco_voz).resolve()
    if not pasta_referencia.is_relative_to(raiz_inputs):
        raise ValueError(f"Item {indice}: banco_voz deve apontar para dentro de inputs/.")
    if not pasta_referencia.is_dir():
        raise FileNotFoundError(f"Item {indice}: banco de voz não encontrado: {pasta_referencia}")
    if not list(pasta_referencia.glob("*.wav")):
        raise FileNotFoundError(f"Item {indice}: não há arquivos WAV em {pasta_referencia}.")

    return {
        **item,
        "data": data,
        "formato": formato,
        "persona": str(item.get("persona") or "MEIO-TERMO").strip(),
        "dominio": str(item.get("dominio") or "GERAL").strip(),
        "lente": str(item.get("lente") or "AUTOMATICO").strip(),
        "elemento": item.get("elemento"),
        "linha_mutante": bool(item.get("linha_mutante", False)),
        "banco_voz": banco_voz,
    }


def respeitar_intervalo_gemini() -> None:
    """Mantém ao menos 10 s entre inícios de chamadas; o gerador também pausa após sucesso."""
    global _ultima_chamada_gemini
    agora = time.monotonic()
    if _ultima_chamada_gemini is not None:
        espera = INTERVALO_GEMINI_SEGUNDOS - (agora - _ultima_chamada_gemini)
        if espera > 0:
            logger.info("Aguardando %.1f s antes da próxima chamada Gemini.", espera)
            time.sleep(espera)
    _ultima_chamada_gemini = time.monotonic()


def selecionar_backgrounds(backgrounds: list[str], quantidade: int) -> list[str]:
    if not backgrounds:
        raise FileNotFoundError("Nenhum background disponível em inputs/videos_fundo/.")
    if quantidade <= 0:
        raise ValueError("O roteiro não contém blocos para renderizar.")

    if len(backgrounds) >= quantidade:
        return random.sample(backgrounds, quantidade)

    selecionados = list(backgrounds)
    while len(selecionados) < quantidade:
        selecionados.append(random.choice(backgrounds))
    random.shuffle(selecionados)
    return selecionados


def processar_item(item: dict[str, Any], indice: int) -> dict[str, int]:
    from gerador_gemini import solicitar_roteiro
    from scripts.voz import gerar_audio_oraculo
    from scripts.video_v2 import listar_backgrounds, listar_musicas, montar_video_v2

    logger.info(
        "Item %d: iniciando roteiro | data=%s formato=%s cartas=%s persona=%s dominio=%s lente=%s",
        indice,
        item["data"],
        item["formato"],
        ", ".join(item["cartas"]),
        item["persona"],
        item["dominio"],
        item["lente"],
    )
    respeitar_intervalo_gemini()
    caminho_json = Path(solicitar_roteiro(
        cartas=item["cartas"],
        data_ddmm=item["data"],
        formato=item["formato"],
        persona=item["persona"],
        dominio=item["dominio"],
        lente_selecionada=item["lente"],
        elemento=item["elemento"],
        linha_mutante=item["linha_mutante"],
    )).resolve()

    if not caminho_json.is_file():
        raise FileNotFoundError(f"O gerador não criou o JSON esperado: {caminho_json}")
    
    pasta_projeto = caminho_json.parent
    nome_projeto = pasta_projeto.name

    with caminho_json.open("r", encoding="utf-8") as arquivo:
        dados_roteiro = json.load(arquivo)

    blocos = dados_roteiro.get("roteiro")
    if not isinstance(blocos, list) or not blocos:
        raise ValueError(f"Roteiro inválido ou sem blocos: {caminho_json}")
    
    textos = [bloco["texto"] for bloco in blocos]
    if not all(isinstance(texto, str) and texto.strip() for texto in textos):
        raise ValueError(f"Há bloco sem texto no roteiro: {caminho_json}")

    # Extrai o gancho do primeiro bloco para o tracker
    gancho_texto = blocos[0].get("texto", "") if blocos else "Gerado via lote noturno"

    # Mapeamento do formato do lote para o formato aceito pelo DB ('video', 'carrossel', 'story')
    formato_lote = item["formato"]
    formato_db = "video"
    if "CARROSSEL" in formato_lote:
        formato_db = "carrossel"

    # Mapeamento da persona para os enums aceitos no DB ('terna', 'meio_termo', 'confrontadora')
    persona_lote = item["persona"].lower()
    persona_db = persona_lote if persona_lote in ("terna", "meio_termo", "confrontadora") else "meio_termo"

    backgrounds = listar_backgrounds()
    musicas = listar_musicas()
    if not musicas:
        raise FileNotFoundError("Nenhuma música disponível em inputs/audio_assets/.")

    pasta_saida_video = pasta_projeto / "morning_review"
    pasta_saida_video.mkdir(parents=True, exist_ok=True)
    sucessos_audio = 0
    sucessos_video = 0

    for variacao in VARIACOES_VOZ:
        variante = variacao["nome"]
        logger.info("Item %d: gerando voz %s para %s.", indice, variante, nome_projeto)
        try:
            audio_path = gerar_audio_oraculo(
                textos,
                nome_projeto,
                temperature=variacao["temperature"],
                speed=variacao["speed"],
                sufixo=f"_{variante}",
                pasta_referencia=item["banco_voz"],
            )
            audio_path = Path(audio_path)
            if not audio_path.is_file() or audio_path.stat().st_size == 0:
                raise FileNotFoundError(f"WAV não foi criado corretamente: {audio_path}")
            sucessos_audio += 1
            logger.info("Item %d: áudio %s salvo em %s.", indice, variante, audio_path)
        except Exception:
            logger.exception("Item %d: falha ao gerar áudio %s.", indice, variante)
            continue

        try:
            fundos_video = selecionar_backgrounds(backgrounds, len(blocos))
            musica_path = random.choice(musicas)
            caminho_video = montar_video_v2(
                audio_path=str(audio_path),
                roteiro_cenas=blocos,
                pasta_saida=str(pasta_saida_video),
                nome_projeto=nome_projeto,
                backgrounds_por_bloco=fundos_video,
                musica_path=musica_path,
                musica_volume=0.12,
                sufixo=f"_{variante}",
            )
            logger.info(
                "Item %d: vídeo %s salvo em %s | BGs=%d | música=%s.",
                indice,
                variante,
                caminho_video,
                len(fundos_video),
                musica_path,
            )
            sucessos_video += 1

            # --- INTEGRAÇÃO COM O ORÁCULO TRACKER ---
            try:
                # Garante que a pasta e a conexão com o banco existem
                os.makedirs(os.path.dirname(CAMINHO_DB), exist_ok=True)
                conexao = sqlite3.connect(CAMINHO_DB)
                cursor = conexao.cursor()

                # Garante que a tabela de posts existe (substitui o antigo init_db())
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        data_publicacao TEXT,
                        plataforma TEXT,
                        formato TEXT,
                        tipo_conteudo TEXT,
                        persona TEXT,
                        banco_voz TEXT,
                        ganchos TEXT,
                        tipo_cta TEXT,
                        observacoes TEXT
                    )
                """)
                
                data_pub = item["data"]
                if "/" in data_pub:
                    partes = data_pub.split("/")
                    data_pub = f"{date.today().year}-{partes[1]}-{partes[0]}"

                # Inserção direta via SQL (substitui o dicionário e a função create_post externa)
                cursor.execute("""
                    INSERT INTO posts (
                        data_publicacao, plataforma, formato, tipo_conteudo, 
                        persona, banco_voz, ganchos, tipo_cta, observacoes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data_pub,
                    "tiktok",
                    formato_db,
                    "leitura_tematica",
                    persona_db,
                    item["banco_voz"],
                    gancho_texto[:200],
                    "descoberta",
                    f"Gerado automaticamente pelo lote noturno - Projeto: {nome_projeto} ({variante})"
                ))

                conexao.commit()
                conexao.close()
                print("✅ Registro salvo com sucesso no Oráculo Tracker!")

            except Exception as e:
                print(f"⚠️ Erro ao registrar no banco de dados do Tracker: {e}")
                
                data_pub = item["data"]
                if "/" in data_pub:
                    partes = data_pub.split("/")
                    data_pub = f"{date.today().year}-{partes[1]}-{partes[0]}"

                novo_post = {
                    "data_publicacao": data_pub,
                    "plataforma": "tiktok",
                    "formato": formato_db,
                    "tipo_conteudo": "leitura_tematica",
                    "persona": persona_db,
                    "banco_voz": item["banco_voz"],
                    "ganchos": gancho_texto[:200],
                    "tipo_cta": "descoberta",
                    "observacoes": f"Gerado automaticamente pelo lote noturno - Projeto: {nome_projeto} ({variante})"
                }
                
                post_id = create_post(novo_post)
                logger.info("Item %d: Post ID %d registrado com sucesso no Oráculo Tracker.", indice, post_id)
            except Exception:
                logger.exception("Item %d: Falha ao registrar o post no banco de dados do Tracker.", indice)

        except Exception:
            logger.exception("Item %d: falha ao renderizar vídeo %s.", indice, variante)

    return {"audios": sucessos_audio, "videos": sucessos_video}


def executar_lote(caminho_lote: Path = LOTE_PATH) -> int:
    configurar_log()
    os.chdir(ROOT)
    logger.info("=== Início do lote noturno ===")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "Your_key_here":
        logger.error("GEMINI_API_KEY não está definida para esta tarefa do Windows.")
        return 2

    try:
        itens = carregar_lote(caminho_lote)
    except Exception:
        logger.exception("Não foi possível carregar o arquivo de lote.")
        return 2

    if not itens:
        logger.info("O lote está vazio; nenhuma tarefa para executar.")
        return 0

    total_audios = 0
    total_videos = 0
    falhas = 0

    itens_pendentes = list(itens)

    for indice, entrada in enumerate(itens, start=1):
        try:
            item = validar_item(entrada, indice)
            resultado = processar_item(item, indice)
            total_audios += resultado["audios"]
            total_videos += resultado["videos"]
            
            if resultado["videos"] < len(VARIACOES_VOZ):
                falhas += 1
                logger.warning(
                    "Item %d concluído parcialmente: %d/3 vídeos e %d/3 áudios.",
                    indice,
                    resultado["videos"],
                    resultado["audios"],
                )
            else:
                logger.info("Item %d concluído: 3 áudios e 3 vídeos.", indice)

            itens_pendentes.pop(0)
            salvar_lote_pendente(itens_pendentes, caminho_lote)

        except Exception:
            falhas += 1
            logger.exception("Item %d falhou; interrompendo lote para preservar pendentes.", indice)
            break

    logger.info(
        "=== Lote encerrado: %d processados, %d áudios, %d vídeos gerados ===",
        len(itens) - len(itens_pendentes),
        total_audios,
        total_videos,
    )
    return 1 if falhas else 0


def main() -> int:
    try:
        return executar_lote()
    except Exception:
        configurar_log()
        logger.exception("Falha não tratada no executor do lote.")
        return 1


if __name__ == "__main__":
    sys.exit(main())