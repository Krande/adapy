import pprint

from ada.fem.formats.abaqus.read import cards


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
    for m in cards.connector_behaviour.regex.finditer(conbeh):
        d = m.groupdict()
        print(d)


def test_shell2solid(shell2solids):
    for m in cards.sh2so_re.regex.finditer(shell2solids):
        d = m.groupdict()
        print(d)


def test_couplings(couplings):
    for m in cards.coupling.regex.finditer(couplings):
        d = m.groupdict()
        print(d)


def test_surfaces(surfaces):
    for m in cards.surface.regex.finditer(surfaces):
        d = m.groupdict()
        print(d)


def test_contact_pairs(interactions):
    for m in cards.contact_pairs.regex.finditer(interactions):
        d = m.groupdict()
        print(d)


def test_contact_general(interactions):
    for m in cards.contact_general.regex.finditer(interactions):
        d = m.groupdict()
        pprint.pprint(d, indent=4)
