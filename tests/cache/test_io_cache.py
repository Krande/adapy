import time

from ada import Assembly, Part


def cache_validation(a, b):
    amats = [mat for p in a.get_all_parts_in_assembly(True) for mat in p.materials]
    bmats = [mat for p in b.get_all_parts_in_assembly(True) for mat in p.materials]
    assert len(amats) == len(bmats)

    bsecs = [sec for p in b.get_all_parts_in_assembly(True) for sec in p.sections]
    asecs = [sec for p in a.get_all_parts_in_assembly(True) for sec in p.sections]
    assert len(asecs) == len(bsecs)

    bbeams = [bm for p in b.get_all_parts_in_assembly(True) for bm in p.beams]
    abeams = [bm for p in a.get_all_parts_in_assembly(True) for bm in p.beams]
    assert len(abeams) == len(bbeams)

    for nA, nB in zip(a.fem.nodes, b.fem.nodes):
        assert nA == nB

    for nA, nB in zip(a.fem.elements, b.fem.elements):
        assert nA == nB

    print(a)
    print(b)


def test_simplestru_fem_cache(bm_ipe300):

    model_name = "ParamAssembly"

    start = time.time()
    pfem = Part("ParamModel") / bm_ipe300
    a = Assembly(model_name, clear_cache=True, enable_cache=True) / pfem

    pfem.fem = pfem.to_fem_obj(0.1)
    time1 = time.time() - start

    a.cache_store.update_cache(a)
    start = time.time()
    b = Assembly(model_name, enable_cache=True)
    time2 = time.time() - start
    cache_validation(a, b)

    print(f"Model generation time reduced from {time1:.2f}s to {time2:.2f}s -> {time1 / time2:.2f} x Improvement")
