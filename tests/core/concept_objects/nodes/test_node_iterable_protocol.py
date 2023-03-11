def test_iter(nodes, contained3nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    i = iter(contained3nodes)
    assert next(i) == n2
    assert next(i) == n1
    assert next(i) == n3


def test_for_loop(nodes, contained3nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    expected = [n2, n1, n3]
    for i, item in enumerate(contained3nodes):
        assert item == expected[i]
