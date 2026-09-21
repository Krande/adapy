r"""A test-only asset provider whose source format core has no reader for.

This exists to keep the layering honest. Its source is newline-delimited JSON with a private
schema: a header line naming the vendor's own axes, then one line per node using the vendor's own
field names ("ref", "up", "title", "cat"). Core never reads it -- the provider turns it into
core's hierarchy/manifest schemas at PUBLISH time, and from then on the built-in ``published``
provider serves the result.

The CI gate that makes this load-bearing:

    git grep -n "fixture-lines\|\.jsonl" src/ada/assets src/ada/comms/rest   ->  nothing

If core ever grows knowledge of this format, that grep fails and the layering has been broken.
"""

from tests.core.assets.fixture_provider.provider import (
    FIXTURE_PROVIDER_ID,
    FixtureLinesProvider,
    publish_fixture,
    register_fixture_provider,
)

__all__ = [
    "FIXTURE_PROVIDER_ID",
    "FixtureLinesProvider",
    "publish_fixture",
    "register_fixture_provider",
]
