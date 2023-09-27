from ada.fem.formats.tools import get_tools


def test_get_all_tools():
    tools = get_tools()
    assert len(tools) == 2
