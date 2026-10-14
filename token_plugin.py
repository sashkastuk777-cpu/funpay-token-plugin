# -*- coding: utf-8 -*-
"""
token_plugin.py — модуль («плагин») для твоего FunPay-бота на Python.

Что делает:
  1. Получает факт покупки (ты вызываешь handle_purchase(...) из своего бота).
  2. Сам определяет, сколько токенов купили: из ID лота (таблица amounts.lots)
     или прямо из текста — «2кк», «2kk», «2000000», «2.5 млн» и т.п.
  3. Заходит на сайт ПОД ТВОИМ АКАУНТОМ (Sashka) и создаёт ключ
     на нужное количество токенов.
  4. Возвращает PurchaseResult с готовым текстом (ключом), который бот
     отправляет покупателю в чат FunPay.

  Покупателю аккаунт на сайте НЕ создаётся — он получает только ключ.

ВАЖНО: по умолчанию включён dry_run = True — сайт НЕ трогается, ключи
«фиктивные». Выключи dry_run в config.yaml, когда пропишешь свои эндпоинты.
"""

from __future__ import annotations

import logging
import random
import re
import string
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
import yaml

__version__ = "1.0.0"

log = logging.getLogger("token_plugin")

# ----------------------------------------------------------------------------
# Разбор количества токенов из текста: «2кк», «2 kk», «2000000», «2.5 млн»
# ----------------------------------------------------------------------------
MULTIPLIERS = {
    "ккк": 1_000_000_000, "kkk": 1_000_000_000, "б": 1_000_000_000, "b": 1_000_000_000,
    "кк": 1_000_000, "kk": 1_000_000, "млн": 1_000_000, "mln": 1_000_000,
    "м": 1_000_000, "m": 1_000_000,
    "к": 1_000, "k": 1_000,
}

_NUM_RE = re.compile(
    r"(?<![a-zа-я0-9])(\d+(?:[.,]\d+)?)\s*(ккк|кк|млн|м|к|kkk|kk|mln|m|k|б|b)(?![a-zа-я0-9])",
    re.IGNORECASE,
)


def parse_tokens(text: Any) -> Optional[int]:
    """Достаёт количество токенов из строки. Возвращает None, если не понял."""
    if not text:
        return None
    t = str(text).lower()
    m = _NUM_RE.search(t)
    if m:
        return int(float(m.group(1).replace(",", ".")) * MULTIPLIERS[m.group(2).lower()])
    # просто большое число: 2000000
    m = re.search(r"\d{4,}", t.replace(" ", ""))
    if m:
        return int(m.group(0))
    return None


def _rand(n: int = 10) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


# ----------------------------------------------------------------------------
# Результат обработки покупки
# ----------------------------------------------------------------------------
@dataclass
class PurchaseResult:
    ok: bool
    key: Optional[str] = None          # сам ключ (или None, если не создавался)
    amount: Optional[int] = None       # сколько токенов
    message: str = ""                  # готовый текст для отправки покупателю
    duplicate: bool = False            # True — этот заказ уже обрабатывался
    error: Optional[str] = None        # текст ошибки (для логов/админа)


class SiteError(Exception):
    """Ошибка при работе с сайтом (логин, регистрация, создание ключа)."""


# ----------------------------------------------------------------------------
# Настройки по умолчанию (config.yaml поверх них накладывается)
# ----------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "site": {
        "base_url": "",                 # например https://my-site.ru
        "dry_run": True,                # True = ничего не трогаем, только тест
        "api_token": "",                # если есть API-ключ сайта — логин/пароль не нужен
        "api_token_header": "Authorization",
        "api_token_prefix": "Bearer ",
        "timeout": 20,
        "retries": 3,
        "login": {                      # используется, только если api_token пустой
            "path": "/api/login",
            "username": "",             # логин админа на сайте
            "password": "",
            "username_field": "login",
            "password_field": "password",
            "success_re": "",           # regex по ответу = «залогинился» (необязательно)
        },
        "create_key": {
            "path": "/api/keys/create",
            "method": "POST",           # POST или GET
            "as_json": False,           # True — слать JSON, False — form-data
            "amount_field": "amount",   # имя поля с количеством токенов
            "username_field": "username",  # "" — если поле с юзернеймом слать не нужно
            "key_json_path": "key",     # куда смотреть в JSON-ответе: "key" или "data.key"
            "key_regex": "",            # fallback: regex для текстового ответа
            "generate_locally": False,  # True — сайт ждёт готовый ключ от тебя
            "key_template": "TOK-{amount}-{rand8}",
            "extra_fields": {},
        },
    },
    "amounts": {
        # ID лота FunPay -> сколько токенов даёт лот (узнать ID можно в URL лота)
        "lots": {},
        "parse_from_text": True,        # понимать «2кк» / «2000000» из названия/текста
    },
    "messages": {
        "success": "✅ Оплата получена! Вам начислено {amount_formatted} токенов.\n🔑 Ваш ключ: {key}",
        "no_amount": "⚠️ Не смог определить количество токенов в заказе. Напишите администратору.",
        "error": "⚠️ Произошла ошибка при выдаче ключей, администратор уже в курсе.",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ----------------------------------------------------------------------------
# HTTP-клиент сайта
# ----------------------------------------------------------------------------
class SiteClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.base = (self.cfg.get("base_url") or "").rstrip("/")
        self.dry_run = bool(self.cfg.get("dry_run", True))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) token-plugin/1.0"
        })
        self.mode = "token" if self.cfg.get("api_token") else "login"
        self._logged_in = False
        self._lock = threading.RLock()

    # --- низкоуровневый запрос с ретраями -----------------------------------
    def _request(self, method: str, path: str, **kw):
        if not self.base:
            raise SiteError("В config.yaml не заполнен site.base_url")
        headers = dict(kw.pop("headers", {}) or {})
        if self.mode == "token":
            headers[self.cfg.get("api_token_header", "Authorization")] = (
                self.cfg.get("api_token_prefix", "Bearer ") + str(self.cfg.get("api_token"))
            )
        url = self.base + path
        timeout = int(self.cfg.get("timeout", 20))
        retries = max(1, int(self.cfg.get("retries", 3)))
        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                r = self.session.request(method, url, timeout=timeout, headers=headers, **kw)
                if r.status_code >= 500:
                    raise SiteError(f"HTTP {r.status_code} на {path}")
                return r
            except (requests.RequestException, SiteError) as e:
                last_err = e
                log.warning("Запрос %s %s — попытка %s/%s не удалась: %s", method, path, attempt, retries, e)
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
        raise SiteError(f"Сайт не отвечает ({path}): {last_err}")

    def _api(self, method: str, path: str, **kw):
        """Запрос с автоматическим перелогином при 401/403."""
        r = self._request(method, path, **kw)
        if r.status_code in (401, 403) and self.mode == "login":
            with self._lock:
                self._do_login()
            r = self._request(method, path, **kw)
        return r

    # --- логин ---------------------------------------------------------------
    def login(self):
        with self._lock:
            self._do_login()

    def _do_login(self):
        if self.mode == "token":
            self._logged_in = True
            return
        lc = self.cfg.get("login") or {}
        if not lc.get("username"):
            raise SiteError("Пустые site.login.username / password в config.yaml")
        r = self._request("POST", lc.get("path", "/api/login"), data={
            lc.get("username_field", "login"): lc["username"],
            lc.get("password_field", "password"): lc.get("password", ""),
        })
        ok_re = lc.get("success_re")
        if ok_re and not re.search(ok_re, r.text):
            raise SiteError("Не удалось залогиниться на сайт — проверь логин/пароль и success_re")
        self._logged_in = True
        log.info("Успешный вход на сайт %s", self.base)

    # --- ключи ---------------------------------------------------------------
    def create_key(self, amount: int, username: str = "") -> str:
        if self.dry_run:
            log.info("[dry-run] ключ на %s токенов для '%s'", amount, username)
            return f"DRYRUN-{amount}-{_rand(6).upper()}"
        cc = self.cfg.get("create_key") or {}

        if cc.get("generate_locally"):
            return cc.get("key_template", "TOK-{amount}-{rand8}").format(
                amount=amount, username=username, rand8=_rand(8).upper())

        data: Dict[str, Any] = {cc.get("amount_field", "amount"): amount}
        if cc.get("username_field"):
            data[cc["username_field"]] = username
        data.update(cc.get("extra_fields") or {})

        method = str(cc.get("method", "POST")).upper()
        if method == "GET":
            r = self._api("GET", cc.get("path", "/api/keys/create"), params=data)
        else:
            r = self._api("POST", cc.get("path", "/api/keys/create"),
                          json=data if cc.get("as_json") else None,
                          data=None if cc.get("as_json") else data)
        if r.status_code >= 400:
            raise SiteError(f"Не удалось создать ключ (HTTP {r.status_code}): {r.text[:200]}")

        key = self._extract_key(r)
        if not key:
            raise SiteError(f"Сайт ответил, но ключ не найден. Ответ: {r.text[:300]}")
        log.info("Создан ключ на %s токенов для '%s': %s", amount, username, key)
        return key

    def _extract_key(self, r) -> Optional[str]:
        cc = self.cfg.get("create_key") or {}
        try:
            data = r.json()
            cur: Any = data
            for part in str(cc.get("key_json_path", "key")).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and cur.strip():
                return cur.strip()
        except ValueError:
            pass
        rx = cc.get("key_regex")
        if rx:
            m = re.search(rx, r.text)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        return None


# ----------------------------------------------------------------------------
# Сам плагин
# ----------------------------------------------------------------------------
class TokenPlugin:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.site = SiteClient(cfg.get("site") or {})
        self._seen: Dict[str, PurchaseResult] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, path: str = "config.yaml") -> "TokenPlugin":
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cls(_deep_merge(DEFAULTS, cfg))

    # ------------------------------------------------------------------ main
    def handle_purchase(
        self,
        *,
        order_id: Optional[Any] = None,   # ID заказа FunPay — защита от дублей
        buyer_username: str = "",         # юзернейм покупателя на FunPay
        amount: Optional[int] = None,     # сколько токенов купили (если уже известно)
        lot_id: Optional[Any] = None,     # ID лота — возьмёт из amounts.lots
        item_name: str = "",              # название купленного лота
        message_text: str = "",           # текст сообщения покупателя
    ) -> PurchaseResult:
        """
        Точка входа. Вызывай её из своего бота, когда пришла покупка.
        Возвращает PurchaseResult — отправляй result.message покупателю.
        """
        key = str(order_id) if order_id is not None else None
        if key:
            with self._lock:
                if key in self._seen:
                    prev = self._seen[key]
                    return PurchaseResult(prev.ok, prev.key, prev.amount, prev.message,
                                          duplicate=True, error=prev.error)
        try:
            result = self._process(buyer_username, amount, lot_id, item_name, message_text)
        except SiteError as e:
            log.exception("Ошибка при обработке покупки (заказ %s)", order_id)
            result = PurchaseResult(False, error=str(e), message=self._msg("error"))
        except Exception as e:  # noqa: BLE001 — бот не должен падать из-за модуля
            log.exception("Непредвиденная ошибка модуля ключей")
            result = PurchaseResult(False, error=str(e), message=self._msg("error"))
        if key:
            with self._lock:
                self._seen[key] = result
                while len(self._seen) > 500:
                    self._seen.pop(next(iter(self._seen)))
        return result

    # ----------------------------------------------------------- внутренняя
    def _process(self, buyer, amount, lot_id, item_name, message_text) -> PurchaseResult:
        ac = self.cfg.get("amounts") or {}
        if amount is None and lot_id is not None:
            amount = (ac.get("lots") or {}).get(str(lot_id))
        if amount is None and ac.get("parse_from_text", True):
            amount = parse_tokens(item_name) or parse_tokens(message_text)
        if not amount or int(amount) <= 0:
            return PurchaseResult(False, error="Не удалось определить количество токенов",
                                  message=self._msg("no_amount"))
        amount = int(amount)
        fmt = _fmt(amount)

        # Вход на сайт — под ТВОИМ аккаунтом (Sashka). Покупателю аккаунт
        # на сайте не нужен: создаём ключ и отдаём его ему в чат.
        key = self.site.create_key(amount, buyer or "")
        msg = self._msg("success", amount_formatted=fmt, key=key)
        return PurchaseResult(True, key=key, amount=amount, message=msg)

    def _msg(self, name: str, **kwargs) -> str:
        templates = self.cfg.get("messages") or {}
        tmpl = templates.get(name) or DEFAULTS["messages"].get(name, "")
        try:
            return tmpl.format(**kwargs)
        except (KeyError, IndexError):
            return tmpl
