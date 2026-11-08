"""config.yaml -> typed config."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent

Goal = Literal["tjedan", "dugorocno"]


class LeagueCfg(BaseModel):
    kljuc: str
    naziv: str
    oznaka: str
    league_id: int
    team_id: int
    privatna: bool = False
    cilj: Goal = "tjedan"
    cilj_po_tjednu: dict[int, Goal] = Field(default_factory=dict)

    def goal_for(self, period: int) -> Goal:
        return self.cilj_po_tjednu.get(period, self.cilj)


class ModelCfg(BaseModel):
    min_minuta: float = 15
    dtd_faktor: float = 0.85
    prag_izgubljen: float = -150
    min_dobitak: float = 6
    rezerva_preuzimanja: int = 1
    min_dobitak_dugorocno: float = 2.5
    zastita_fp: float = 4
    vijesti_sati: float = 72
    kandidata_po_ligi: int = 40
    vatra_min_fp: float = 12
    vatra_jedan_signal_fp: float = 20


class ClaudeCfg(BaseModel):
    ukljuceno: bool = False
    model: str = "claude-sonnet-5-5"


class Config(BaseModel):
    sezona: int = 2027
    vremenska_zona: str = "Europe/Zagreb"
    prvi_dan: dt.date = dt.date(2026, 10, 20)
    dashboard_url: str = ""
    izvjestaji: dict[str, str] = Field(default_factory=lambda: {"jutro": "08:30", "vecer": "21:30"})
    preuzimanja_tjedno: int = 7
    lige: list[LeagueCfg]
    model: ModelCfg = Field(default_factory=ModelCfg)
    claude: ClaudeCfg = Field(default_factory=ClaudeCfg)

    def league(self, key: str) -> LeagueCfg:
        for lc in self.lige:
            if lc.kljuc == key:
                return lc
        raise KeyError(key)


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
