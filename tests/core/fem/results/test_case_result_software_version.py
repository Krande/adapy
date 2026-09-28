"""A cached case keeps the version of the solver that produced it.

A report built from the cache has to state the version its numbers came from, not the version of
whatever solver happens to be installed on the machine replaying it.
"""

from dataclasses import dataclass

from ada.fem.results import FeaCaseResult


@dataclass
class _Result:
    software_version: str


def test_version_is_taken_from_the_live_result_and_survives_the_cache(tmp_path):
    case = FeaCaseResult(name="case", fem_format="calculix", results=_Result("2.23"))
    assert case.software_version == "2.23"

    case.save_to_json(tmp_path / "case")
    back = FeaCaseResult.from_json(tmp_path / "case.json")
    assert back.results is None
    assert back.software_version == "2.23"


def test_a_reader_that_found_no_version_means_unknown(tmp_path):
    assert FeaCaseResult(name="case", fem_format="sesam", results=_Result("N/A")).software_version is None


def test_a_cache_written_before_the_field_existed_reads_as_unknown(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"name": "old", "fem_format": "abaqus", "metadata": {}, "last_modified": 0}')
    assert FeaCaseResult.from_json(path).software_version is None
