from __future__ import annotations

import threading

import pytest

from buscateses.config import Paths
from buscateses.favorites import Favorites, FavoritesError


@pytest.fixture
def favorites(tmp_path) -> Favorites:
    return Favorites(tmp_path)


def test_add_list_contains_and_remove(favorites: Favorites) -> None:
    assert favorites.add("capes:0001", "ler com atenção") is True
    assert favorites.add("capes:0002") is True
    assert favorites.contains("capes:0001") is True
    assert favorites.contains("capes:9999") is False
    assert favorites.count() == 2
    entries = {item["id"]: item for item in favorites.list()}
    assert entries["capes:0001"]["note"] == "ler com atenção"
    assert entries["capes:0002"]["note"] is None
    assert entries["capes:0001"]["added_at"]

    assert favorites.remove("capes:0001") is True
    assert favorites.remove("capes:0001") is False
    assert favorites.count() == 1
    assert favorites.contains("capes:0001") is False


def test_duplicate_updates_note_without_inserting(favorites: Favorites) -> None:
    assert favorites.add("capes:0001", "primeira") is True
    assert favorites.add("capes:0001", "segunda") is False
    assert favorites.count() == 1
    assert favorites.add("capes:0001") is False
    entry = favorites.list()[0]
    assert entry["note"] == "segunda"


def test_blank_identifier_is_rejected(favorites: Favorites) -> None:
    with pytest.raises(FavoritesError):
        favorites.add("   ")
    with pytest.raises(FavoritesError):
        favorites.remove("")
    with pytest.raises(FavoritesError):
        favorites.contains("\n")


def test_data_persists_between_instances(tmp_path) -> None:
    first = Favorites(tmp_path)
    first.add("capes:0007", "persistente")
    second = Favorites(tmp_path)
    assert second.contains("capes:0007")
    assert second.list()[0]["note"] == "persistente"


def test_paths_integration(tmp_path) -> None:
    paths = Paths.from_home(tmp_path / "home")
    favorites = Favorites(paths)
    assert favorites.path == paths.user_db
    favorites.add("capes:0042")
    assert paths.user_db.is_file()


def test_thread_safety(favorites: Favorites) -> None:
    errors: list[BaseException] = []

    def worker(start: int) -> None:
        try:
            for index in range(start, start + 20):
                favorites.add(f"capes:{index:05d}")
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index * 20,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []
    assert favorites.count() == 80
