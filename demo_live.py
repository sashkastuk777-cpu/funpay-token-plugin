# -*- coding: utf-8 -*-
"""
demo_live.py — проверка боевой логики (dry-run, сайт не трогается).
Гоняет весь диапазон сумм: от 5 млн (автовозврат) до 1 млрд (ключ).

Запуск:  python demo_live.py
"""
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from token_plugin import TokenPlugin

logging.basicConfig(level=logging.WARNING)  # WARNING+ — чтобы вывод был чистым

plugin = TokenPlugin.from_config("config.yaml")

# Привязываем возврат на FunPay через твоего бота.
# Замени на реальный вызов своего бота, например:
#   plugin.set_funpay_refund(lambda oid, amt, buyer: client.refund(oid))
plugin.set_funpay_refund(lambda oid, amt, buyer: print(
    f"    >>> [FunPay] возврат заказа {oid} на {amt} токенов (покупатель {buyer})") or True)

# (название, item_name, ожидание)
CASES = [
    ("5 млн (< 10 млн)",         "5кк токенов",   "refund"),
    ("9 999 999 (< 10 млн)",     "9999999",       "refund"),
    ("2 млн (классика)",         "2кк токенов",   "refund"),
    ("10 млн (ровно минимум)",   "10кк токенов",  "key"),
    ("10 000 001 (> минимума)",  "10000001",      "key"),
    ("500 млн",                  "500кк токенов", "key"),
    ("1 млрд (максимум)",        "1ккк токенов",  "key"),
]

passed = failed = 0
for i, (label, item, expect) in enumerate(CASES, 1):
    r = plugin.handle_purchase(
        order_id=f"fp_40{i}",
        buyer_username="Тестер",
        item_name=item,
    )
    got = "refund" if r.refund else "key"
    mark = "OK " if got == expect and r.ok else "FAIL"
    if got == expect and r.ok:
        passed += 1
    else:
        failed += 1
    print(f"[{mark}] {label:28} -> {got:6} (ждём {expect})")
    if got == "refund":
        print(f"        уведомление админу: {r.admin_note}")
    else:
        print(f"        ключ: {r.key}")

print()
print("=" * 50)
print(f"Итого: {passed} OK, {failed} FAIL")

print()
print("=" * 50)
print("Пример сообщений покупателю (как увидит в Telegram):")
r_key = plugin.handle_purchase(order_id="fp_5001", buyer_username="A", item_name="20кк")
r_ref = plugin.handle_purchase(order_id="fp_5002", buyer_username="B", item_name="2кк")
print("--- ключ ---")
print(r_key.message)
print("--- возврат ---")
print(r_ref.message)
