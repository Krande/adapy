from __future__ import annotations

import logging
import pathlib


def get_results_from_result_file(file_ref, overwrite=False, results=None):
    from .concepts import Results

    file_ref = pathlib.Path(file_ref)
    suffix = file_ref.suffix.lower()

    res_reader, fem_format = Results.res_map.get(suffix, (None, None))

    if res_reader is None:
        logging.error(f'Results class currently does not support filetype "{suffix}"')
        return None

    return res_reader(results, file_ref, overwrite)


def from_results_file(fem_res: str | pathlib.Path, fem_format: str = None):

    _ = get_results_from_result_file(fem_res, fem_format)

    get_results_from_result_file()
