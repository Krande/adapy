from ada.core.guid import create_guid


def test_roundtrip_guid():
    input_name = "MyVeryUniqueName"
    guid = create_guid(input_name)
    assert guid == "2UCj6U$_x2Q6_NuoWvOvBz"
