from dataclasses import dataclass, field
from typing import Optional
import streamlit as st


@dataclass
class ItemFilaRoteiro:
    cartas: str
    persona: str = "MEIO-TERMO"
    tipo: Optional[str] = None
    formato: str = "MANHA"
    dominio: str = "GERAL"
    lente: str = "AUTOMATICO"
    elemento: Optional[str] = None
    linha_mutante: bool = False
    data: Optional[str] = None 
    modelo: str = "gemini-3.5-flash"

    def __post_init__(self):
        # Sincroniza 'tipo' e 'formato' para manter total retrocompatibilidade
        if self.tipo and not self.formato:
            self.formato = self.tipo
        elif self.formato and not self.tipo:
            self.tipo = self.formato


@dataclass
class ItemFilaVoz:
    roteiro_path: str
    nome_projeto: str
    referencia_voz: str = "voz/oraculo_original"
    preset: str = "Padrão"
    temperatura: float = 0.88
    velocidade: float = 1.12
    n_geracoes: int = 3


@dataclass
class Receita:
    nome_projeto: str
    roteiro_path: str
    voz_path: str
    backgrounds_por_bloco: list[str]
    musica_path: Optional[str] = None
    musica_volume: float = 0.18


def init_state():
    """Inicializa o session_state com as três filas e o estado das abas."""
    defaults = {
        "fila_roteiros":  [],   # list[ItemFilaRoteiro]
        "fila_vozes":     [],   # list[ItemFilaVoz]
        "fila_receitas":  [],   # list[Receita]
        "executando":     False,
        "log_execucao":   [],
        # estado compartilhado entre abas
        "roteiro_selecionado":  None,   # path do JSON
        "roteiro_dados":        None,   # conteúdo parsed
        "voz_selecionada":      None,   # path do wav
        "bgs_selecionados":     [],     # list[str] um por bloco
        "musica_selecionada":   None,   # path
        "musica_volume": 0.18,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def adicionar_roteiro(item: ItemFilaRoteiro):
    st.session_state.fila_roteiros.append(item)


def adicionar_voz(item: ItemFilaVoz):
    st.session_state.fila_vozes.append(item)


def adicionar_receita(receita: Receita):
    st.session_state.fila_receitas.append(receita)


def remover_roteiro(idx: int):
    st.session_state.fila_roteiros.pop(idx)


def remover_voz(idx: int):
    st.session_state.fila_vozes.pop(idx)


def remover_receita(idx: int):
    st.session_state.fila_receitas.pop(idx)


def total_pendente() -> int:
    return (
        len(st.session_state.fila_roteiros) +
        len(st.session_state.fila_vozes) +
        len(st.session_state.fila_receitas)
    )