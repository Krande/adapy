from ada.cadit.step import step2glb_capi, step2glb_to_glb


def test_both_step2glb_routes_raise_the_same_exception():
    # An `except` written against either route has to catch a failure from the other.
    assert step2glb_to_glb.Step2GlbUnavailable is step2glb_capi.Step2GlbUnavailable
