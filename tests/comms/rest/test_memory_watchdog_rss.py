"""The conversion/bake memory watchdog bounds memory the kernel cannot reclaim.

A SIN bake memory-maps its field cache; reading it back fills ``RssFile`` with clean
file-backed pages the kernel drops under pressure. Counting them (``VmRSS``) killed a bake
that fitted its pod, so the watchdog reads ``RssAnon + RssShmem``.
"""

from ada.comms.rest.subprocess_convert import _unreclaimable_rss_kb

# The shape of /proc/<pid>/status at the peak of that bake: 4.1 GB resident, 1.4 GB of it heap.
STATUS = """\
Name:\tpython
VmPeak:\t 9876543 kB
VmHWM:\t 4222000 kB
VmRSS:\t 4222000 kB
RssAnon:\t 1462000 kB
RssFile:\t 2759000 kB
RssShmem:\t    1000 kB
VmSwap:\t       0 kB
"""


def test_file_backed_pages_are_not_counted():
    assert _unreclaimable_rss_kb(STATUS) == 1462000 + 1000


def test_shmem_is_counted():
    # tmpfs pages (a cache directory on a memory-backed volume) are charged to the pod.
    status = STATUS.replace("RssShmem:\t    1000 kB", "RssShmem:\t 2000000 kB")
    assert _unreclaimable_rss_kb(status) == 1462000 + 2000000


def test_falls_back_to_vmrss_without_rss_breakdown():
    status = "Name:\tpython\nVmRSS:\t  123456 kB\n"
    assert _unreclaimable_rss_kb(status) == 123456


def test_nothing_to_read():
    assert _unreclaimable_rss_kb("Name:\tpython\n") is None
