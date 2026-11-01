"""A configuracao do usuario: as vozes que o agente le (ate 20) e o limite de risco.

O destaque da bounty pede um agente que monitora vozes ESCOLHIDAS PELO USUARIO e cria a posicao
sob limites de risco DEFINIDOS PELO USUARIO. Aqui isso e um arquivo (`config/voices.json`) que o
usuario edita, validado em voz alta (um erro nao vira uma voz a menos em silencio) e com o hash
gravado em todo dossier: trocar uma voz ou afrouxar o limite vira um fato registrado, nao uma
mudanca invisivel.

Duas fontes, as duas sem chave: `rss` (RSS ou Atom) e `telegram` (preview publico de um canal em
https://t.me/s/<canal>). O X nao entra de forma nativa: a API e paga. Uma ponte RSS publica para uma
conta do X entra como `rss`, sem tocar no resto.
"""

import hashlib
import json
import re
from pathlib import Path

from . import agents_v2, canonical

MAX_VOICES = 20
KINDS = ("rss", "telegram")
TELEGRAM_URL = re.compile(r"^https://t\.me/s/[A-Za-z0-9_]{4,64}$")
RSS_URL = re.compile(r"^https://[^\s/@]+(?:/\S*)?$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&_'-]{0,39}$")
CONFIG_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "voices.json"


class ConfigError(ValueError):
    """A configuracao nao vale: a mensagem diz qual campo e por que."""


def validate(cfg) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("the config must be a JSON object")
    if cfg.get("version") != CONFIG_VERSION:
        raise ConfigError(f"unsupported config version {cfg.get('version')!r}, expected {CONFIG_VERSION}")
    voices = cfg.get("voices")
    if not isinstance(voices, list) or not voices:
        raise ConfigError("voices must be a non-empty list")
    if len(voices) > MAX_VOICES:
        raise ConfigError(f"at most {MAX_VOICES} voices, got {len(voices)}")
    seen: set[str] = set()
    for i, voice in enumerate(voices):
        where = f"voices[{i}]"
        if not isinstance(voice, dict):
            raise ConfigError(f"{where} must be an object")
        name, kind, url = voice.get("name"), voice.get("kind"), voice.get("url")
        if not isinstance(name, str) or not NAME.match(name):
            raise ConfigError(f"{where}.name must be 1-40 characters of letters, digits and . & _ ' - (got {name!r})")
        if name.lower() in seen:
            raise ConfigError(f"{where}.name {name!r} repeats: one voice must count once in a convergence")
        seen.add(name.lower())
        if kind not in KINDS:
            raise ConfigError(f"{where}.kind must be one of {KINDS} (got {kind!r})")
        if not isinstance(url, str):
            raise ConfigError(f"{where}.url must be a string")
        if kind == "telegram" and not TELEGRAM_URL.match(url):
            raise ConfigError(f"{where}.url must look like https://t.me/s/<channel> (got {url!r})")
        if kind == "rss" and not RSS_URL.match(url):
            raise ConfigError(f"{where}.url must be an https URL without credentials (got {url!r})")
    risk = cfg.get("risk", {})
    if not isinstance(risk, dict):
        raise ConfigError("risk must be an object")
    pct = risk.get("max_risk_pct")
    if pct is not None:
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            raise ConfigError(f"risk.max_risk_pct must be a number (got {pct!r})")
        try:
            agents_v2.with_risk_pct(agents_v2.DEFAULT, pct)
        except ValueError as err:
            raise ConfigError(f"risk.max_risk_pct: {err}") from None


def fingerprint(cfg: dict) -> str:
    """Hash estavel da configuracao inteira, em JSON canonico (o mesmo dos dossiers)."""
    return hashlib.sha256(canonical.canonical(cfg)).hexdigest()


def load(path: Path | str | None = None) -> dict:
    """Le e valida. Devolve as vozes como {nome: url}, o risco pedido e o registro para o dossier."""
    target = Path(path) if path else DEFAULT_PATH
    try:
        cfg = json.loads(target.read_text(encoding="utf-8"))
    except OSError as err:
        raise ConfigError(f"cannot read {target}: {err}") from None
    except ValueError as err:
        raise ConfigError(f"{target} is not valid JSON: {err}") from None
    validate(cfg)
    kinds = {k: sum(1 for v in cfg["voices"] if v["kind"] == k) for k in KINDS}
    pct = (cfg.get("risk") or {}).get("max_risk_pct")
    return {
        "feeds": {v["name"]: v["url"] for v in cfg["voices"]},
        "risk_pct": float(pct) if pct is not None else None,
        "record": {
            "sha256": fingerprint(cfg),
            "voices": len(cfg["voices"]),
            "kinds": {k: n for k, n in kinds.items() if n},
            "max_risk_pct": float(pct) if pct is not None else None,
        },
    }
