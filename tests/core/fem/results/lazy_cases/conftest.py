"""Decks with unstored load combinations, each baked twice: eagerly (every
combination superposed from the raw records, the pre-lazy behaviour) and as a
lazy base bake (stored cases only)."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import pytest

from ada.fem.results.artefacts import bake_fea_artefacts_from_source

from .synthetic_sin import SyntheticDeck, build_fixture_deck, build_synthetic_deck

FIXTURE_SIN = (
    pathlib.Path(__file__).resolve().parents[5]
    / "files"
    / "fem_files"
    / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
)


@dataclass
class BakedDeck:
    deck: SyntheticDeck
    eager_dir: pathlib.Path
    base_dir: pathlib.Path
    eager: dict
    base: dict


def _bake(deck: SyntheticDeck, root: pathlib.Path) -> BakedDeck:
    eager = bake_fea_artefacts_from_source(deck.path, root / "eager", lazy_combinations=False)
    base = bake_fea_artefacts_from_source(deck.path, root / "base")
    return BakedDeck(
        deck=deck,
        eager_dir=root / "eager",
        base_dir=root / "base",
        eager=json.loads(eager.manifest_path.read_text(encoding="utf-8")),
        base=json.loads(base.manifest_path.read_text(encoding="utf-8")),
    )


@pytest.fixture(scope="session")
def fixture_deck(tmp_path_factory) -> BakedDeck:
    root = tmp_path_factory.mktemp("lazy-fixture")
    return _bake(build_fixture_deck(FIXTURE_SIN, root / "deck.SIN"), root)


@pytest.fixture(scope="session")
def synthetic_deck(tmp_path_factory) -> BakedDeck:
    root = tmp_path_factory.mktemp("lazy-synthetic")
    return _bake(build_synthetic_deck(root / "deck.SIN"), root)


@pytest.fixture(scope="session", params=["fixture", "synthetic"])
def baked_deck(request) -> BakedDeck:
    return request.getfixturevalue(f"{request.param}_deck")
