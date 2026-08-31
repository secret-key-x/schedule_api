import json
import os
from geometric_parser import PDFGeometricTableParser
from text_processor import ScheduleTextProcessor

# 1. Задаємо шлях до PDF
pdf_path = os.path.join("temp", "K2v3 (1).pdf")

print("⏳ 1. Ініціалізація та запуск геометричного парсера...")

# Приклад використання геометрік парсера (як у самому низу файла, без циклів)
parser = PDFGeometricTableParser(pdf_path, text_gap_threshold=5.0)
parsed_cells = parser.parse_table_geometric(page_index=0)
schedule_data = parser.export_schedule_to_json()

print("✅ Геометричний парсер відпрацював успішно.")

print("\n⏳ 2. Застосування ScheduleTextProcessor...")

# Використовуємо текст процесор на отриманих даних
processor = ScheduleTextProcessor()
clean_schedule = processor.process_schedule(schedule_data)

# Зберігаємо очищений результат у JSON
output_path = "clean_schedule.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(clean_schedule, f, ensure_ascii=False, indent=2)

print(f"✅ Готово! Результат обробки збережено у файл '{output_path}'")