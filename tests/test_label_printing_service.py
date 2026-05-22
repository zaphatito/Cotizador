import src.label_printing_service as lps
from src.label_printing_service import ZplEtiqueta, generar_zpl_etiqueta


def test_label_zpl_does_not_print_black_border():
    zpl = generar_zpl_etiqueta(
        ZplEtiqueta(nombre="LCDPDDLADYMILLION", codigo="CC183", gramos="2ATT20260422"),
        "^GFA,0,0,0,",
    )

    assert "^LL136" in zpl
    assert "^A0N,30,30" in zpl
    assert "^GB" not in zpl


def test_label_batch_places_two_labels_in_one_physical_row(monkeypatch):
    monkeypatch.setattr(lps, "image_to_zpl_gfa", lambda *args, **kwargs: ("^GFA,0,0,0,", 0, 0))

    zpl = lps.generar_zpl_lote(
        [
            ZplEtiqueta(nombre="UNO", codigo="DD001", gramos="50g"),
            ZplEtiqueta(nombre="DOS", codigo="DD002", gramos="50g"),
        ],
        logo_path="ignored",
    )

    assert zpl.count("^XA") == 1
    assert "^PW824" in zpl
    assert "^LL136" in zpl
    assert "^FO14,25" in zpl
    assert "^FODD001" not in zpl
    assert "^FO438,25" in zpl
    assert "^FDDD001^FS" in zpl
    assert "^FDDD002^FS" in zpl
    assert "^GB" not in zpl


def test_label_text_keeps_logo_fixed_and_right_aligns_grams():
    zpl = generar_zpl_etiqueta(
        ZplEtiqueta(
            nombre="LCDP DD212MUJER EXTRA LARGO",
            codigo="DD001",
            gramos="50g",
        ),
        "^GFA,0,0,0,",
    )

    assert "^FDLCDP DD212MUJER EXTRA LARGO^FS" in zpl
    assert "^FO317,26" in zpl
    assert "^A0N,52,52" in zpl
    assert "^FB120,1,0,R,0" in zpl


def test_label_counts_report_requested_and_physical_labels_for_two_columns():
    labels = [ZplEtiqueta(nombre=f"ITEM {i}", codigo=f"DD{i:03d}", gramos="50g") for i in range(25)]

    requested = lps.count_requested_labels(labels)
    expected_physical = lps.expected_physical_labels_for_count(requested)
    effective_requested = lps.effective_requested_labels_for_printed(
        requested_labels=requested,
        printed_labels=expected_physical,
    )

    assert requested == 25
    assert expected_physical == 26
    assert effective_requested == 25


def test_label_counts_cap_requested_when_printer_reports_partial_print():
    requested = 30
    printed = 20

    assert lps.effective_requested_labels_for_printed(
        requested_labels=requested,
        printed_labels=printed,
    ) == 20
