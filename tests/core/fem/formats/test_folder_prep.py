"""A re-run's analysis folder starts empty, even when the trash cannot take the old one."""

import pytest

from ada.fem.formats import utils


def _old_run(tmp_path):
    old = tmp_path / "case"
    old.mkdir()
    (old / "case.rmed").write_bytes(b"old result")
    return old


def test_an_old_run_is_deleted_when_the_trash_refuses_it(tmp_path, monkeypatch):
    """Analyses run side by side race to create the trash folder; the loser's old run used to stay,
    and Code_Aster then failed on the result file it found there."""
    import send2trash

    def refuse(path):
        raise FileExistsError(17, "File exists", "/root/.local/share/Trash/files")

    monkeypatch.setattr(send2trash, "send2trash", refuse)
    monkeypatch.setattr(utils.Config(), "general_safe_deletion", True, raising=False)
    _old_run(tmp_path)
    folder = utils.folder_prep(tmp_path, "case", overwrite=True)
    assert folder.is_dir() and not any(folder.iterdir())


def test_an_old_run_that_cannot_be_removed_stops_the_analysis(tmp_path, monkeypatch):
    _old_run(tmp_path)
    monkeypatch.setattr(utils.Config(), "general_safe_deletion", False, raising=False)
    monkeypatch.setattr(utils.shutil, "rmtree", lambda *a, **k: None)
    with pytest.raises(IOError, match="Could not remove the previous analysis"):
        utils.folder_prep(tmp_path, "case", overwrite=True)
