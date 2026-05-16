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
