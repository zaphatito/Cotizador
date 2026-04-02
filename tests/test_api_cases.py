import src.api.cases as cases


def test_resolve_api_base_url_uses_local_in_dev(monkeypatch):
    monkeypatch.setattr(cases.sys, "frozen", False, raising=False)

    base_url = cases._resolve_api_base_url()

    assert base_url == cases.API_BASE_URL_DEV
    assert cases.build_api_cases(base_url) == (
        (cases.API_CASE_LOGIN, "http://localhost:3000/service/sessions/busLogin"),
        (cases.API_CASE_POST_PRESUPUESTO, "http://localhost:3000/service/db/postPresupuesto"),
        (cases.API_CASE_VERIFY_COTIZADOR, "http://localhost:3000/service/db/verifyCotizador"),
    )


def test_resolve_api_base_url_uses_prod_in_frozen_build(monkeypatch):
    monkeypatch.setattr(cases.sys, "frozen", True, raising=False)

    base_url = cases._resolve_api_base_url()

    assert base_url == cases.API_BASE_URL_PROD
    assert cases.build_api_cases(base_url) == (
        (cases.API_CASE_LOGIN, "http://efperfumes.online:3005/service/sessions/busLogin"),
        (cases.API_CASE_POST_PRESUPUESTO, "http://efperfumes.online:3005/service/db/postPresupuesto"),
        (cases.API_CASE_VERIFY_COTIZADOR, "http://efperfumes.online:3005/service/db/verifyCotizador"),
    )
