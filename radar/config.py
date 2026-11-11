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
    dtd_faktor: float = 0.85          # v2; zamijenjeno s p_play (ostaje da stari config.yaml radi)
    prag_izgubljen: float = -150
    min_dobitak: float = 6
    rezerva_preuzimanja: int = 1
    min_dobitak_dugorocno: float = 2.5
    zastita_fp: float = 4
    vijesti_sati: float = 72
    kandidata_po_ligi: int = 40
    vatra_min_fp: float = 12
    vatra_jedan_signal_fp: float = 20
    tezine_sada: tuple[float, float, float] = (0.45, 0.35, 0.20)          # sezona, zadnjih 7, projekcija
    tezine_dugo: tuple[float, float, float] = (0.35, 0.30, 0.35)
    tezine_bez_projekcije: tuple[float, float, float] = (0.5, 0.3, 0.2)   # zadnjih 7, zadnjih 15, sezona
    trend_minuta_prag: float = 4
    trend_bonus: float = 0.5


class PPlayCfg(BaseModel):
    """Vjerojatnost da igrač zaista igra utakmicu (p_play)."""
    active: float = 0.97
    dtd: float = 0.6
    dtd_kasnije: float = 0.85
    upitan: float = 0.5
    b2b: float = 0.10
    b2b_rizik: float = 0.25
    b2b_dob: int = 33
    povratak: float = 0.7

    @classmethod
    def neutral(cls) -> "PPlayCfg":
        """Everyone healthy plays every game (planner tests that are about timing, not availability)."""
        return cls(active=1.0, dtd=1.0, dtd_kasnije=1.0, upitan=1.0, b2b=0.0, b2b_rizik=0.0, povratak=1.0)


class AlarmCfg(BaseModel):
    interval_min: float = 15
    interval_utakmice_min: float = 10
    jaki_vlasnistvo: float = 30
    jaki_top: int = 40
    pred_utakmicu_min: list[int] = Field(default_factory=lambda: [60, 30])
    prag_sanse_protivnik: float = 0.05
    espn_ne_radi_min: float = 30
    tjedni_pregled: str = "ned 22:00"


class SimCfg(BaseModel):
    broj: int = 5000
    sd_udio: float = 0.3
    seed: int = 20261111
    savjet_ispod: float = 0.20
    savjet_iznad: float = 0.90


class ServerCfg(BaseModel):
    lokalno: str = "data/local"
    objavi_dashboard: bool = True
    podaci_grana: str = "podaci"
    repo: str = "arminsahic260-del/waiver-radar"
    pc_svjez_sati: float = 3        # GitHub Actions preskače svoj prolaz ako je PC objavio podatke unutar ovoliko sati


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
    p_play: PPlayCfg = Field(default_factory=PPlayCfg)
    alarmi: AlarmCfg = Field(default_factory=AlarmCfg)
    simulacija: SimCfg = Field(default_factory=SimCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)
    claude: ClaudeCfg = Field(default_factory=ClaudeCfg)

    @property
    def local_dir(self) -> Path:
        p = Path(self.server.lokalno)
        return p if p.is_absolute() else ROOT / p

    def league(self, key: str) -> LeagueCfg:
        for lc in self.lige:
            if lc.kljuc == key:
                return lc
        raise KeyError(key)


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
