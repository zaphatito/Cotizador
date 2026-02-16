from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:
    from ..logging_setup import get_logger
except Exception:  # pragma: no cover
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


_CHARSET_RE = re.compile(r"charset=([\w\-]+)", re.IGNORECASE)
_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "token",
}


@dataclass(frozen=True)
class ApiResponse:
    ok: bool
    status_code: int
    method: str
    case: int
    url: str
    elapsed_ms: int
    headers: dict[str, str]
    data: Any | None
    text: str


class ApiValidationError(ValueError):
    pass


class ApiCaseNotFoundError(KeyError):
    pass


class ApiRequestError(RuntimeError):
    def __init__(self, message: str, *, response: ApiResponse | None = None):
        super().__init__(message)
        self.response = response


class GenericApiController:
    _ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
    _METHODS_WITH_BODY = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(
        self,
        *,
        cases: tuple[tuple[int, str], ...],
        default_headers: Mapping[str, Any] | None = None,
        default_timeout: float = 20.0,
        logger_name: str = "src.api.generic_controller",
    ):
        self._log = get_logger(logger_name)
        self._default_timeout = self._validate_timeout(default_timeout, field_name="default_timeout")
        self._default_headers = self._normalize_headers(default_headers or {})
        self._cases_map = self._parse_cases(cases)
        if self._cases_map:
            self._log.info("API controller listo | cases=%s", sorted(self._cases_map.keys()))
        else:
            self._log.debug("API controller listo sin cases configurados.")

    def set_cases(self, cases: tuple[tuple[int, str], ...]) -> None:
        self._cases_map = self._parse_cases(cases)
        if self._cases_map:
            self._log.info("API controller actualizado | cases=%s", sorted(self._cases_map.keys()))
        else:
            self._log.debug("API controller actualizado sin cases configurados.")

    def request(
        self,
        method: str,
        case: int,
        *,
        params: Mapping[str, Any] | None = None,
        data: dict[str, Any] | list[Any] | str | bytes | bytearray | None = None,
        json_data: Any | None = None,
        headers: Mapping[str, Any] | None = None,
        timeout: float | int | None = None,
        expected_status: Sequence[int] | None = (200, 201, 202, 204),
        path_params: Mapping[str, Any] | None = None,
        raise_for_status: bool = True,
    ) -> ApiResponse:
        normalized_method = self._normalize_method(method)
        normalized_case = self._normalize_case(case)
        url = self._build_url(normalized_case, params=params, path_params=path_params)
        timeout_value = self._validate_timeout(
            self._default_timeout if timeout is None else timeout,
            field_name="timeout",
        )
        headers_final = self._default_headers.copy()
        headers_final.update(self._normalize_headers(headers or {}))
        expected_set = self._normalize_expected_status(expected_status)

        body_bytes = self._build_body(normalized_method, data=data, json_data=json_data, headers=headers_final)
        if body_bytes is not None:
            headers_final.setdefault("Content-Length", str(len(body_bytes)))

        safe_headers = self._sanitize_headers(headers_final)
        self._log.debug(
            "API request | method=%s case=%s url=%s timeout=%s headers=%s",
            normalized_method,
            normalized_case,
            url,
            timeout_value,
            safe_headers,
        )

        req = urllib.request.Request(url, data=body_bytes, headers=headers_final, method=normalized_method)
        start = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=timeout_value) as resp:
                status_code = int(getattr(resp, "status", resp.getcode()))
                raw_body = resp.read()
                response_headers = dict(resp.headers.items())
                response = self._build_response(
                    method=normalized_method,
                    case=normalized_case,
                    url=url,
                    status_code=status_code,
                    raw_body=raw_body,
                    headers=response_headers,
                    elapsed_ms=self._elapsed_ms(start),
                    expected_status=expected_set,
                )
        except urllib.error.HTTPError as exc:
            raw_body = b""
            try:
                raw_body = exc.read() or b""
            except Exception:
                raw_body = b""

            response = self._build_response(
                method=normalized_method,
                case=normalized_case,
                url=url,
                status_code=int(getattr(exc, "code", 0) or 0),
                raw_body=raw_body,
                headers=dict(getattr(exc, "headers", {}) or {}),
                elapsed_ms=self._elapsed_ms(start),
                expected_status=expected_set,
            )
            self._log.warning(
                "API HTTP error | method=%s case=%s status=%s elapsed_ms=%s url=%s body=%s",
                normalized_method,
                normalized_case,
                response.status_code,
                response.elapsed_ms,
                url,
                self._compact_text(response.text),
            )
            if raise_for_status:
                raise ApiRequestError(
                    f"HTTP {response.status_code} en case={normalized_case} ({normalized_method} {url})",
                    response=response,
                ) from exc
            return response
        except urllib.error.URLError as exc:
            elapsed_ms = self._elapsed_ms(start)
            self._log.error(
                "API network error | method=%s case=%s elapsed_ms=%s url=%s reason=%s",
                normalized_method,
                normalized_case,
                elapsed_ms,
                url,
                getattr(exc, "reason", exc),
            )
            raise ApiRequestError(
                f"Error de red en case={normalized_case} ({normalized_method} {url}): {getattr(exc, 'reason', exc)}"
            ) from exc
        except Exception as exc:
            elapsed_ms = self._elapsed_ms(start)
            self._log.exception(
                "API unexpected error | method=%s case=%s elapsed_ms=%s url=%s",
                normalized_method,
                normalized_case,
                elapsed_ms,
                url,
            )
            raise ApiRequestError(
                f"Error inesperado en case={normalized_case} ({normalized_method} {url}): {exc}"
            ) from exc

        self._log.info(
            "API response | method=%s case=%s status=%s elapsed_ms=%s url=%s",
            normalized_method,
            normalized_case,
            response.status_code,
            response.elapsed_ms,
            url,
        )
        if (not response.ok) and raise_for_status:
            raise ApiRequestError(
                f"Respuesta no esperada status={response.status_code} en case={normalized_case} ({normalized_method} {url})",
                response=response,
            )
        return response

    def get(self, case: int, **kwargs) -> ApiResponse:
        return self.request("GET", case, **kwargs)

    def post(self, case: int, **kwargs) -> ApiResponse:
        return self.request("POST", case, **kwargs)

    def put(self, case: int, **kwargs) -> ApiResponse:
        return self.request("PUT", case, **kwargs)

    def patch(self, case: int, **kwargs) -> ApiResponse:
        return self.request("PATCH", case, **kwargs)

    def delete(self, case: int, **kwargs) -> ApiResponse:
        return self.request("DELETE", case, **kwargs)

    def _parse_cases(self, cases: tuple[tuple[int, str], ...]) -> dict[int, str]:
        if not isinstance(cases, tuple):
            raise ApiValidationError("`cases` debe ser una tupla de tuplas: ((1, 'https://...'), ...)")

        out: dict[int, str] = {}
        for idx, item in enumerate(cases):
            if not isinstance(item, tuple) or len(item) != 2:
                raise ApiValidationError(f"Case invalido en posicion {idx}: debe ser (case, url)")
            raw_case, raw_url = item
            case = self._normalize_case(raw_case)
            url = str(raw_url or "").strip()
            if not url:
                raise ApiValidationError(f"Case {case}: url vacia")
            self._validate_url_template(url, case=case)
            if case in out:
                raise ApiValidationError(f"Case duplicado: {case}")
            out[case] = url

        if not out:
            self._log.debug("API controller sin cases configurados.")
        return out

    def _validate_url_template(self, url: str, *, case: int) -> None:
        # Acepta placeholders para path params: /items/{id}
        check_url = url
        for placeholder in re.findall(r"{[^{}]+}", url):
            check_url = check_url.replace(placeholder, "x")

        parsed = urllib.parse.urlparse(check_url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ApiValidationError(f"Case {case}: url debe iniciar con http:// o https://")
        if not parsed.netloc:
            raise ApiValidationError(f"Case {case}: url invalida, falta host")

    def _build_url(
        self,
        case: int,
        *,
        params: Mapping[str, Any] | None,
        path_params: Mapping[str, Any] | None,
    ) -> str:
        if case not in self._cases_map:
            raise ApiCaseNotFoundError(f"No existe URL configurada para case={case}")

        url_template = self._cases_map[case]
        url = url_template

        if path_params:
            formatted: dict[str, str] = {}
            for key, value in path_params.items():
                key_s = str(key or "").strip()
                if not key_s:
                    raise ApiValidationError("`path_params` contiene una llave vacia")
                if value is None:
                    raise ApiValidationError(f"`path_params[{key_s}]` no puede ser None")
                formatted[key_s] = urllib.parse.quote(str(value), safe="")
            try:
                url = url_template.format(**formatted)
            except KeyError as exc:
                missing = str(exc).strip("'")
                raise ApiValidationError(f"Falta `path_params['{missing}']` para case={case}") from exc

        if "{" in url or "}" in url:
            raise ApiValidationError(f"Case {case}: la URL aun tiene placeholders sin reemplazar: {url}")

        q_params = self._normalize_params(params or {})
        if q_params:
            q = urllib.parse.urlencode(q_params, doseq=True)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{q}"
        return url

    def _build_body(
        self,
        method: str,
        *,
        data: dict[str, Any] | list[Any] | str | bytes | bytearray | None,
        json_data: Any | None,
        headers: dict[str, str],
    ) -> bytes | None:
        if (data is not None) and (json_data is not None):
            raise ApiValidationError("No puedes enviar `data` y `json_data` al mismo tiempo.")

        if (data is not None or json_data is not None) and method not in self._METHODS_WITH_BODY:
            raise ApiValidationError(f"El metodo {method} no soporta body en este controlador.")

        if json_data is not None:
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
            try:
                return json.dumps(json_data, ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                raise ApiValidationError(f"`json_data` no se pudo serializar a JSON: {exc}") from exc

        if data is None:
            return None
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, str):
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
            return data.encode("utf-8")
        if isinstance(data, (dict, list)):
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
            try:
                return json.dumps(data, ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                raise ApiValidationError(f"`data` no se pudo serializar a JSON: {exc}") from exc

        raise ApiValidationError("`data` debe ser dict, list, str, bytes, bytearray o None.")

    def _build_response(
        self,
        *,
        method: str,
        case: int,
        url: str,
        status_code: int,
        raw_body: bytes,
        headers: Mapping[str, str],
        elapsed_ms: int,
        expected_status: set[int] | None,
    ) -> ApiResponse:
        headers_dict = {str(k): str(v) for k, v in dict(headers).items()}
        text = self._decode_body(raw_body, headers_dict)
        data = self._parse_payload(text, headers_dict)
        ok_by_http = 200 <= status_code < 300
        ok_by_expected = True if not expected_status else (status_code in expected_status)
        return ApiResponse(
            ok=bool(ok_by_http and ok_by_expected),
            status_code=status_code,
            method=method,
            case=case,
            url=url,
            elapsed_ms=elapsed_ms,
            headers=headers_dict,
            data=data,
            text=text,
        )

    def _decode_body(self, raw_body: bytes, headers: Mapping[str, str]) -> str:
        if not raw_body:
            return ""
        charset = self._extract_charset(headers.get("Content-Type", ""))
        if charset:
            try:
                return raw_body.decode(charset, errors="replace")
            except Exception:
                pass
        try:
            return raw_body.decode("utf-8", errors="replace")
        except Exception:
            return raw_body.decode("latin-1", errors="replace")

    def _parse_payload(self, text: str, headers: Mapping[str, str]) -> Any | None:
        if not text:
            return None
        content_type = str(headers.get("Content-Type", "")).lower()
        payload = text.strip()
        if ("json" in content_type) or payload.startswith("{") or payload.startswith("["):
            try:
                return json.loads(payload)
            except Exception:
                return None
        return None

    def _normalize_method(self, method: str) -> str:
        m = str(method or "").strip().upper()
        if m not in self._ALLOWED_METHODS:
            raise ApiValidationError(f"Metodo invalido: {method}. Permitidos: {sorted(self._ALLOWED_METHODS)}")
        return m

    def _normalize_case(self, case: Any) -> int:
        if isinstance(case, bool) or not isinstance(case, int):
            raise ApiValidationError("`case` debe ser un entero positivo.")
        if case <= 0:
            raise ApiValidationError("`case` debe ser mayor a 0.")
        return case

    def _normalize_headers(self, headers: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(headers, Mapping):
            raise ApiValidationError("`headers` debe ser un dict/map.")
        out: dict[str, str] = {}
        for key, value in headers.items():
            key_s = str(key or "").strip()
            if not key_s:
                raise ApiValidationError("`headers` contiene una llave vacia.")
            if value is None:
                continue
            out[key_s] = str(value)
        return out

    def _normalize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise ApiValidationError("`params` debe ser un dict/map.")
        out: dict[str, Any] = {}
        for key, value in params.items():
            key_s = str(key or "").strip()
            if not key_s:
                raise ApiValidationError("`params` contiene una llave vacia.")
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                out[key_s] = [self._normalize_scalar(v, field=f"params[{key_s}]") for v in value if v is not None]
            else:
                out[key_s] = self._normalize_scalar(value, field=f"params[{key_s}]")
        return out

    def _normalize_scalar(self, value: Any, *, field: str) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value
        raise ApiValidationError(f"{field} solo acepta str, int, float o bool.")

    def _normalize_expected_status(self, expected_status: Sequence[int] | None) -> set[int] | None:
        if expected_status is None:
            return None
        out: set[int] = set()
        for code in expected_status:
            if isinstance(code, bool) or not isinstance(code, int):
                raise ApiValidationError("`expected_status` debe contener enteros HTTP.")
            if code < 100 or code > 599:
                raise ApiValidationError(f"Codigo HTTP invalido en expected_status: {code}")
            out.add(code)
        return out

    def _validate_timeout(self, value: Any, *, field_name: str) -> float:
        try:
            timeout = float(value)
        except Exception as exc:
            raise ApiValidationError(f"`{field_name}` debe ser numerico (>0).") from exc
        if timeout <= 0:
            raise ApiValidationError(f"`{field_name}` debe ser mayor a 0.")
        return timeout

    def _sanitize_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        safe: dict[str, str] = {}
        for key, value in headers.items():
            key_l = str(key).lower()
            if key_l in _SENSITIVE_HEADERS:
                safe[key] = "***"
            else:
                safe[key] = value
        return safe

    def _extract_charset(self, content_type: str) -> str:
        m = _CHARSET_RE.search(content_type or "")
        if not m:
            return ""
        return m.group(1).strip().lower()

    def _compact_text(self, text: str, *, limit: int = 320) -> str:
        t = (text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(t) <= limit:
            return t
        return f"{t[:limit]}..."

    def _elapsed_ms(self, start: float) -> int:
        return int((time.perf_counter() - start) * 1000)
