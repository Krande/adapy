from ada.fem.formats.abaqus.read import cards
from ada.fem.formats.abaqus.read.read_sections import conn_from_groupdict


def test_consec(consec):
    assertions = [
        {
            "elset": "Wire-1-Set-1",
            "behavior": "ConnProp-1_VISC_DAMPER_ELEM",
            "contype": "Bushing,",
            "csys": '"Datum csys-1",',
        },
        {
            "elset": "Wire-2-Set-1",
            "behavior": "ConnProp-1_VISC_DAMPER_ELEM",
            "contype": "Bushing,",
            "csys": '"Datum csys-2",',
        },
    ]
    for i, m in enumerate(cards.connector_section.regex.finditer(consec)):
        d = m.groupdict()
        assert assertions[i] == d


def test_conn_beha(conbeh):
    results = list(cards.connector_behaviour.regex.finditer(conbeh))
    assert len(results) == 1

    result = results[0]
    gd = result.groupdict()
    assert gd["name"] == 'ConnProp-1_VISC_DAMPER_ELEM'
    assert gd["component"] == "1"

    conn = conn_from_groupdict(gd, None)
    assert conn.name == 'ConnProp-1_VISC_DAMPER_ELEM'
    assert len(conn.elastic_comp) == 1


def test_shell2solid(shell2solids):
    for m in cards.sh2so_re.regex.finditer(shell2solids):
        _ = m.groupdict()
        # print(_)


def test_couplings(couplings):
    for m in cards.coupling.regex.finditer(couplings):
        _ = m.groupdict()
        # print(_)


def test_surfaces(surfaces):
    for m in cards.surface.regex.finditer(surfaces):
        _ = m.groupdict()
        # print(_)


def test_contact_pairs(interactions):
    for m in cards.contact_pairs.regex.finditer(interactions):
        _ = m.groupdict()
        # print(_)


def test_contact_general(interactions):
    for m in cards.contact_general.regex.finditer(interactions):
        _ = m.groupdict()
        # pprint.pprint(_, indent=4)
