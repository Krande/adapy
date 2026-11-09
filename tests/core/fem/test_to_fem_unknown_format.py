import pytest

import ada


def test_an_unknown_format_is_refused_by_name(tmp_path):
    with pytest.raises(ValueError, match="no_such_solver.*abaqus"):
        ada.Assembly("a").to_fem("m", "no_such_solver", scratch_dir=tmp_path)
