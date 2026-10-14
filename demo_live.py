# -*- coding: utf-8 -*-
"""
demo_live.py — проверка боевой логики:
бот заходит на сайт под твоим аккаунтом (Sashka) -> создаёт ключ -> отдаёт покупателю.
Никто не регистрируется. Работает с config.yaml (dry-run: сайт не трогается).

Запуск:  python demo_live.py
"""
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from token_plugin import TokenPlugin

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

plugin = TokenPlugin.from_config("config.yaml")

result = plugin.handle_purchase(
    order_id="fp_2001",
    buyer_username="Покупатель123",   # юзернейм покупателя с FunPay
    item_name="2кк токенов",
)

print(f"ok={result.ok}  amount={result.amount}  key={result.key}")
print("сообщение покупателю:")
print(result.message)
