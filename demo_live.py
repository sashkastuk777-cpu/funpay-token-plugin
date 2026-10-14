# -*- coding: utf-8 -*-
"""
demo_live.py — проверка боевой логики (dry-run, сайт не трогается):
  1) покупка >= 10 млн  -> ключ с номером заказа
  2) покупка <  10 млн  -> автовозврат

Запуск:  python demo_live.py
"""
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from token_plugin import TokenPlugin

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

plugin = TokenPlugin.from_config("config.yaml")

print("=" * 50)
print("СЦЕНАРИЙ 1: покупка 20 млн (больше 10 млн) -> ключ")
r1 = plugin.handle_purchase(
    order_id="fp_3001",
    buyer_username="Покупатель123",
    item_name="20кк токенов",
)
print(f"ok={r1.ok}  refund={r1.refund}  order_id={r1.order_id}  key={r1.key}")
print("--- сообщение покупателю (то, что увидит в Telegram) ---")
print(r1.message)
print()

print("=" * 50)
print("СЦЕНАРИЙ 2: покупка 2 млн (меньше 10 млн) -> автовозврат")
r2 = plugin.handle_purchase(
    order_id="fp_3002",
    buyer_username="Покупатель456",
    item_name="2кк токенов",
)
print(f"ok={r2.ok}  refund={r2.refund}  order_id={r2.order_id}  key={r2.key}")
print("--- сообщение покупателю ---")
print(r2.message)
print("--- уведомление админу ---")
print(r2.admin_note)
