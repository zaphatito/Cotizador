import pytest

from src.catalog_context import CatalogScope
from src.quote_context_service import (
    build_quote_context,
    resolve_historical_quote_owner,
    resolve_historical_quote_scope,
)
from src.widgets_parts import catalog_scope_dialog


class _CatalogManagerStub:
    username = "vendedor"
    id_cotizador = "C01"

    @staticmethod
    def scope_record(_scope):
        return {"base_currency": "pen"}


def test_build_quote_context_uses_scope_identity_and_cached_currency():
    scope = CatalogScope("pe", "ef perfumes")

    context = build_quote_context(_CatalogManagerStub(), scope)

    assert context.scope == CatalogScope("PE", "EF PERFUMES")
    assert context.username == "vendedor"
    assert context.id_cotizador == "C01"
    assert context.base_currency == "PEN"
    assert context.stock_policy == "INFORMATIONAL"


def test_historical_scope_preserves_an_exact_modern_assignment():
    scope = CatalogScope("PE", "EF PERFUMES")

    resolved, used_legacy_fallback = resolve_historical_quote_scope(
        {
            "country_code": "PERU",
            "company_type": "EF PERFUMES",
            "quote_context_version": 1,
        },
        (scope,),
        default_country_code="PY",
    )

    assert resolved == scope
    assert used_legacy_fallback is False


def test_historical_scope_uses_unique_authorized_company_for_legacy_quote():
    authorized = CatalogScope("PE", "LA CASA DEL PERFUME")

    resolved, used_legacy_fallback = resolve_historical_quote_scope(
        {
            "country_code": "PE",
            "company_type": "EF PERFUMES",
            "quote_context_version": 0,
        },
        (authorized,),
        default_country_code="PY",
    )

    assert resolved == authorized
    assert used_legacy_fallback is True


def test_historical_scope_keeps_modern_removed_assignment_blocked():
    authorized = CatalogScope("PE", "LA CASA DEL PERFUME")

    resolved, used_legacy_fallback = resolve_historical_quote_scope(
        {
            "country_code": "PE",
            "company_type": "EF PERFUMES",
            "quote_context_version": 1,
        },
        (authorized,),
        default_country_code="PY",
    )

    assert resolved is None
    assert used_legacy_fallback is False


def test_historical_scope_does_not_guess_between_two_companies():
    resolved, used_legacy_fallback = resolve_historical_quote_scope(
        {
            "country_code": "PE",
            "company_type": "EMPRESA LEGACY",
            "quote_context_version": 0,
        },
        (
            CatalogScope("PE", "EF PERFUMES"),
            CatalogScope("PE", "LA CASA DEL PERFUME"),
        ),
        default_country_code="PY",
    )

    assert resolved is None
    assert used_legacy_fallback is False


def test_historical_owner_reassigns_legacy_quote_to_current_cotizador():
    owner = resolve_historical_quote_owner(
        {
            "cotizador_username": "lcdp5",
            "id_cotizador": "005",
            "quote_context_version": 0,
        },
        current_username="lcdp6",
        current_id_cotizador="006",
    )

    assert owner == ("lcdp6", "006")


def test_historical_owner_keeps_modern_other_cotizador_blocked():
    owner = resolve_historical_quote_owner(
        {
            "cotizador_username": "lcdp5",
            "id_cotizador": "005",
            "quote_context_version": 1,
        },
        current_username="lcdp6",
        current_id_cotizador="006",
    )

    assert owner is None


@pytest.mark.parametrize('username,code,expected', [
    ('lcdp5', '005', ('lcdp5', '005')),
    ('LCDP5', '005', ('lcdp5', '005')),
    ('lcdp5', '006', None),
    ('lcdp6', '005', None),
])
def test_shared_history_requires_the_same_user_and_code(username, code, expected):
    owner = resolve_historical_quote_owner(
        {'cotizador_username': 'lcdp5', 'id_cotizador': '005',
         'sync_owner_id': '9', 'sync_current_owner_id': '9'},
        current_username=username, current_id_cotizador=code,
    )
    assert owner == expected


class _ScopeManagerStub:
    server_mode = True

    def __init__(self, scopes):
        self.available_scopes = tuple(scopes)
        self.selected = None

    def set_active_scope(self, scope):
        self.selected = scope


def test_scope_selector_blocks_zero_and_uses_single_configuration(monkeypatch, qapp):
    warnings = []
    monkeypatch.setattr(catalog_scope_dialog.QMessageBox, "warning", lambda *_: warnings.append(True))
    assert catalog_scope_dialog.select_catalog_scope(None, _ScopeManagerStub([])) is None
    assert warnings == [True]
    scope = CatalogScope("PE", "EF PERFUMES")
    manager = _ScopeManagerStub([scope])
    manager.stock_matrix = lambda _: {"stores": []}
    manager.catalog_health = lambda _: (True, "")
    prompted = []

    def accept(dialog):
        prompted.append(True)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(catalog_scope_dialog.CatalogScopeDialog, "exec", accept)
    assert catalog_scope_dialog.select_catalog_scope(None, manager) == scope
    assert prompted == []
    assert manager.selected is None


def test_scope_selector_single_configuration_requires_healthy_catalog(monkeypatch, qapp):
    scope = CatalogScope("PE", "EF PERFUMES")
    manager = _ScopeManagerStub([scope])
    manager.catalog_health = lambda _: (False, "Catálogo pendiente de sincronización")
    manager.stock_matrix = lambda _: {"stores": []}
    warnings = []
    monkeypatch.setattr(catalog_scope_dialog.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(catalog_scope_dialog.CatalogScopeDialog, "exec", lambda _: pytest.fail("No debe pedir país con una sola configuración"))
    assert catalog_scope_dialog.select_catalog_scope(None, manager) is None
    assert warnings == ["Catálogo pendiente de sincronización"]
    assert catalog_scope_dialog.select_catalog_scope(None, manager, require_catalog=False) == scope
    assert manager.selected is None


def test_scope_selector_preselects_without_mutation_and_cancel_is_noop(monkeypatch, qapp):
    first = CatalogScope("PE", "EF PERFUMES")
    second = CatalogScope("PY", "LA CASA DEL PERFUME")
    manager = _ScopeManagerStub([first, second])
    manager.stock_matrix = lambda _: {"stores": []}
    manager.catalog_health = lambda _: (True, "")

    def accept(dialog):
        assert dialog.company.currentData() == second
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(catalog_scope_dialog.CatalogScopeDialog, "exec", accept)
    assert catalog_scope_dialog.select_catalog_scope(None, manager, preferred=second) == second
    assert manager.selected is None
    monkeypatch.setattr(catalog_scope_dialog.CatalogScopeDialog, "exec", lambda _: catalog_scope_dialog.QDialog.Rejected)
    assert catalog_scope_dialog.select_catalog_scope(None, manager) is None
    assert manager.selected is None
