# -*- coding: utf-8 -*-
"""
funpay_plugin.py — ОДИН файл-плагин для твоего FunPay-бота.

Что делает:
  • Получает покупку -> понимает сколько токенов купили («2кк», «1ккк», 2000000...)
  • Заходит на твой сайт ПОД ТВОИМ АКАУНТОМ (Sashka) и создаёт ключ
  • Ключ выдаётся с номером заказа
  • Если куплено меньше 10 млн -> АВТОВОЗВРАТ на FunPay (через твоего бота)

Как ставить:
  1) Положи этот файл рядом со своим ботом
  2) Впиши свои настройки в БЛОК НАСТРОЕК ниже (адрес сайта, логин, пароль)
  3) Подключи в боте (см. «ПРИМЕР ПОДКЛЮЧЕНИЯ В БОТА» в самом низу файла)

Зависимости: только `requests` (pip install requests)
"""

import json
import logging
import random
import re
import string
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

__version__ = "2.0.0"

log = logging.getLogger("funpay_plugin")


# ============================================================================
#  БЛОК НАСТРОЕК — ВПИШИ СВОЁ
# ============================================================================
SETTINGS = {
    # --- твой сайт ---
    "base_url": "",                # например "https://my-site.ru" (ОБЯЗАТЕЛЬНО)
    "dry_run": True,               # True = тест без сайта. Поставь False когда всё настроено

    # вход на сайт под ТВОИМ аккаунтом
    "login": "Sashka",
    "password": "smertapi-120612",
    "login_path": "/api/login",         # куда слать логин
    "login_user_field": "login",        # имя поля логина
    "login_pass_field": "password",     # имя поля пароля

    # создание ключа
    "create_key_path": "/api/keys/create",
    "create_key_amount_field": "amount",
    "create_key_user_field": "username",
    "create_key_as_json": False,        # True = слать JSON, False = form
    "key_json_path": "key",             # где ключ в ответе ("key" или "data.key")

    # сессия: если ты уже залогинен — бот подхватит и не будет логиниться снова
    "session_file": "site_session.json",

    # --- автовозврат ---
    "refund_below": 10_000_000,         # меньше этой суммы -> возврат
    "refund_path": "/api/refund",       # эндпоинт возврата на сайте (пусто = только FunPay)

    # --- ID лота -> сколько токенов (если нужно фиксировать) ---
    # пример: "123456789": 2000000
    "lots": {},
}


# ============================================================================
#  НИЖЕ МЕХАНИКА — НЕ ТРОГАЙ
# ============================================================================

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
    """Достаёт количество токенов из строки: «2кк» -> 2000000, «1ккк» -> 1000000000."""
    if not text:
        return None
    t = str(text).lower()
    m = _NUM_RE.search(t)
    if m:
        return int(float(m.group(1).replace(",", ".")) * MULTIPLIERS[m.group(2).lower()])
    m = re.search(r"\d{4,}", t.replace(" ", ""))
    if m:
        return int(m.group(0))
    return None


def _rand(n: int = 10) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


@dataclass
class PurchaseResult:
    ok: bool
    key: Optional[str] = None
    amount: Optional[int] = None
    message: str = ""
    duplicate: bool = False
    error: Optional[str] = None
    refund: bool = False
    order_id: Optional[str] = None
    admin_note: str = ""


class SiteError(Exception):
    pass


class SiteClient:
    def __init__(self, s: Dict[str, Any]):
        self.s = s
        self.base = (s.get("base_url") or "").rstrip("/")
        self.dry_run = bool(s.get("dry_run", True))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        self._logged_in = False
        self._lock = threading.RLock()
        self.session_file = s.get("session_file") or "site_session.json"
        self._load_session()

    # --- сессия ---
    def _load_session(self):
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.session.cookies.update(data.get("cookies") or {})
            if data.get("logged_in"):
                self._logged_in = True
                log.info("Подхвачена активная сессия сайта")
        except (OSError, ValueError):
            pass

    def _save_session(self):
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump({
                    "logged_in": self._logged_in,
                    "cookies": requests.utils.dict_from_cookiejar(self.session.cookies),
                }, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.warning("Не удалось сохранить сессию: %s", e)

    # --- запрос ---
    def _request(self, method: str, path: str, **kw):
        if not self.base:
            raise SiteError("Не заполнен base_url в SETTINGS")
        url = self.base + path
        last_err = None
        for attempt in range(1, 4):
            try:
                r = self.session.request(method, url, timeout=20, **kw)
                if r.status_code >= 500:
                    raise SiteError(f"HTTP {r.status_code} на {path}")
                return r
            except (requests.RequestException, SiteError) as e:
                last_err = e
                if attempt < 3:
                    time.sleep(min(2 ** attempt, 8))
        raise SiteError(f"Сайт не отвечает ({path}): {last_err}")

    def _api(self, method: str, path: str, **kw):
        r = self._request(method, path, **kw)
        if r.status_code in (401, 403):
            with self._lock:
                self._do_login()
            r = self._request(method, path, **kw)
        return r

    # --- логин ---
    def _do_login(self):
        if self.dry_run:
            self._logged_in = True
            return
        r = self._request("POST", self.s.get("login_path", "/api/login"), data={
            self.s.get("login_user_field", "login"): self.s.get("login", ""),
            self.s.get("login_pass_field", "password"): self.s.get("password", ""),
        })
        if r.status_code >= 400:
            raise SiteError(f"Не удалось залогиниться (HTTP {r.status_code})")
        self._logged_in = True
        self._save_session()
        log.info("Успешный вход на сайт %s", self.base)

    # --- ключ ---
    def create_key(self, amount: int, username: str = "") -> str:
        if self.dry_run:
            log.info("[dry-run] ключ на %s токенов для '%s'", amount, username)
            return f"DRYRUN-{amount}-{_rand(6).upper()}"
        data = {
            self.s.get("create_key_amount_field", "amount"): amount,
            self.s.get("create_key_user_field", "username"): username,
        }
        as_json = bool(self.s.get("create_key_as_json"))
        r = self._api("POST", self.s.get("create_key_path", "/api/keys/create"),
                      json=data if as_json else None,
                      data=None if as_json else data)
        if r.status_code >= 400:
            raise SiteError(f"Не удалось создать ключ (HTTP {r.status_code}): {r.text[:200]}")
        key = self._extract_key(r)
        if not key:
            raise SiteError(f"Ключ не найден в ответе: {r.text[:300]}")
        log.info("Создан ключ на %s токенов: %s", amount, key)
        return key

    def _extract_key(self, r) -> Optional[str]:
        try:
            data = r.json()
            cur: Any = data
            for part in str(self.s.get("key_json_path", "key")).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and cur.strip():
                return cur.strip()
        except ValueError:
            pass
        return None

    # --- возврат на сайте ---
    def refund(self, order_id: str, amount: int, buyer: str = "") -> bool:
        path = self.s.get("refund_path") or ""
        if not path or self.dry_run:
            return False
        r = self._api("POST", path, data={"order_id": order_id, "amount": amount, "username": buyer})
        if r.status_code >= 400:
            raise SiteError(f"Возврат не прошёл (HTTP {r.status_code})")
        return True


class FunPayPlugin:
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.s = {**SETTINGS, **(settings or {})}
        self.site = SiteClient(self.s)
        self._seen: Dict[str, PurchaseResult] = {}
        self._lock = threading.Lock()
        self._funpay_refund_fn = None

    # --- возврат на FunPay ---
    def set_funpay_refund(self, fn):
        """
        Привязывает функцию возврата на FunPay. Пример:
            plugin.set_funpay_refund(lambda oid, amt, buyer: client.refund(oid))
        """
        self._funpay_refund_fn = fn

    def _do_funpay_refund(self, order_id: str, amount: int, buyer: str) -> bool:
        if not self._funpay_refund_fn:
            return False
        try:
            ok = bool(self._funpay_refund_fn(order_id, amount, buyer))
            log.info("Возврат на FunPay по заказу %s: %s", order_id, "ок" if ok else "False")
            return ok
        except Exception:
            log.exception("Ошибка функции возврата FunPay (заказ %s)", order_id)
            return False

    # --- главная точка входа ---
    def handle_purchase(
        self,
        *,
        order_id: Optional[Any] = None,
        buyer_username: str = "",
        amount: Optional[int] = None,
        lot_id: Optional[Any] = None,
        item_name: str = "",
        message_text: str = "",
    ) -> PurchaseResult:
        key = str(order_id) if order_id is not None else None
        if key:
            with self._lock:
                if key in self._seen:
                    prev = self._seen[key]
                    return PurchaseResult(prev.ok, prev.key, prev.amount, prev.message,
                                          duplicate=True, error=prev.error,
                                          refund=prev.refund, order_id=prev.order_id,
                                          admin_note=prev.admin_note)
        try:
            result = self._process(order_id, buyer_username, amount, lot_id, item_name, message_text)
        except SiteError as e:
            log.exception("Ошибка обработки покупки (заказ %s)", order_id)
            result = PurchaseResult(False, error=str(e), message="⚠️ Ошибка выдачи ключей, администратор в курсе.",
                                    order_id=str(order_id) if order_id is not None else None)
        except Exception as e:
            log.exception("Непредвиденная ошибка")
            result = PurchaseResult(False, error=str(e), message="⚠️ Ошибка выдачи ключей, администратор в курсе.",
                                    order_id=str(order_id) if order_id is not None else None)
        if key:
            with self._lock:
                self._seen[key] = result
                while len(self._seen) > 500:
                    self._seen.pop(next(iter(self._seen)))
        return result

    def _process(self, order_id, buyer, amount, lot_id, item_name, message_text) -> PurchaseResult:
        oid = str(order_id) if order_id is not None else "—"
        if amount is None and lot_id is not None:
            amount = (self.s.get("lots") or {}).get(str(lot_id))
        if amount is None:
            amount = parse_tokens(item_name) or parse_tokens(message_text)
        if not amount or int(amount) <= 0:
            return PurchaseResult(False, error="Не удалось определить количество токенов",
                                  message="⚠️ Не смог определить количество токенов. Напишите администратору.",
                                  order_id=oid)
        amount = int(amount)
        fmt = _fmt(amount)

        # --- автовозврат ---
        min_amount = int(self.s.get("refund_below") or 0)
        if min_amount and amount < min_amount:
            self._do_funpay_refund(oid, amount, buyer or "")
            self.site.refund(oid, amount, buyer or "")
            log.warning("Автовозврат: заказ %s, %s (< %s)", oid, amount, min_amount)
            return PurchaseResult(
                True, amount=amount, order_id=oid, refund=True,
                message=(f"❌ Заказ отменён, оформляем возврат.\n"
                         f"📦 Заказ: {oid}\n"
                         f"💰 Сумма: {fmt} токенов\n"
                         f"ℹ️ Минимальная сумма покупки — {_fmt(min_amount)} токенов.\n"
                         f"Деньги вернутся на ваш способ оплаты."),
                admin_note=(f"🔁 Автовозврат: заказ {oid}, {fmt} токенов "
                            f"(меньше {_fmt(min_amount)}), покупатель {buyer or '—'}"),
            )

        # --- ключ ---
        key = self.site.create_key(amount, buyer or "")
        msg = (f"✅ Оплата получена!\n"
               f"📦 Заказ: {oid}\n"
               f"💰 Сумма: {fmt} токенов\n"
               f"🔑 Ваш ключ: {key}")
        return PurchaseResult(True, key=key, amount=amount, order_id=oid, message=msg)


# ============================================================================
#  ПРИМЕР ПОДКЛЮЧЕНИЯ В БОТА (раскомментируй и подставь своё)
# ============================================================================
#
# from funpay_plugin import FunPayPlugin
#
# plugin = FunPayPlugin()
#
# # возврат на FunPay через своего бота:
# plugin.set_funpay_refund(lambda oid, amt, buyer: client.refund(oid))
#
# # когда пришла покупка:
# def on_purchase(order_id, buyer, item_name):
#     r = plugin.handle_purchase(order_id=order_id, buyer_username=buyer, item_name=item_name)
#     if r.message:
#         send_to_chat(buyer, r.message)       # отправить покупателю
#     if r.admin_note:
#         send_to_admin(r.admin_note)          # уведомить тебя
#
# # тест без бота:
# if __name__ == "__main__":
#     print(plugin.handle_purchase(order_id="1", buyer_username="A", item_name="20кк").message)
#     print(plugin.handle_purchase(order_id="2", buyer_username="B", item_name="2кк").message)
