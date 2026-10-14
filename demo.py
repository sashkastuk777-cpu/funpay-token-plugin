# -*- coding: utf-8 -*-
"""
demo.py — проверка модуля в dry-run режиме. Сайт НЕ нужен, ничего не ломается.

Запуск:  python demo.py
"""
import logging
import sys

# Windows-консоль по умолчанию cp1251 и не умеет печатать эмодзи — переключаем на UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from token_plugin import TokenPlugin, parse_tokens

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

print("=== Проверка разбора количества токенов ===")
for s in ["2кк токенов", "500к", "2000000", "2.5 млн", "kk 10k", "просто текст"]:
    print(f"  {s!r:25} -> {parse_tokens(s)}")

print("\n=== Симуляция покупки (dry-run) ===")
plugin = TokenPlugin.from_config("config.example.yaml")

result = plugin.handle_purchase(
    order_id="fp_1001",
    buyer_username="Иван2024",
    item_name="2кк токенов",
)
print(f"  ok={result.ok}  amount={result.amount}  key={result.key}")
print(f"  сообщение покупателю:\n  {result.message}")

print("\n=== Повтор того же заказа (не должно создать второй ключ) ===")
dup = plugin.handle_purchase(order_id="fp_1001", buyer_username="Иван2024", item_name="2кк токенов")
print(f"  duplicate={dup.duplicate}  key={dup.key}")

print("\n=== Заказ, где количество определить не удалось ===")
bad = plugin.handle_purchase(order_id="fp_1002", buyer_username="Пётр", item_name="что-то странное")
print(f"  ok={bad.ok}  error={bad.error}  message={bad.message}")
