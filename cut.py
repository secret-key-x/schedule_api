import fitz  # pip install PyMuPDF


def true_crop_pdf(input_path: str, output_path: str, crop_box: tuple):
  """Фізично вирізає область з PDF, видаляючи все за її межами

  на рівні структури файлу через redactions. Текст залишається векторним.

  crop_box: (x0, y0, x1, y1) у пунктах PDF.
  """
  doc = fitz.open(input_path)

  for page in doc:
    page_rect = page.rect
    rect = fitz.Rect(crop_box)

    # Визначаємо 4 зони ЗА МЕЖАМИ нашого crop_box, які треба повністю знищити
    r_top = fitz.Rect(0, 0, page_rect.width, rect.y0)
    r_bottom = fitz.Rect(0, rect.y1, page_rect.width, page_rect.height)
    r_left = fitz.Rect(0, rect.y0, rect.x0, rect.y1)
    r_right = fitz.Rect(rect.x1, rect.y0, page_rect.width, rect.y1)

    # Додаємо анотації редагування для кожної зайвої зони
    for r in [r_top, r_bottom, r_left, r_right]:
      if not r.is_empty:
        page.add_redact_annot(r)

    # Фізично видаляємо все зайве з пам'яті сторінки
    page.apply_redactions()

    # Змінюємо фізичні розміри сторінки під наш виріз
    page.set_mediabox(rect)
    page.set_cropbox(rect)

  doc.save(output_path)
  doc.close()
  print(f"✅ Готово! Справді обрізаний файл збережено як: {output_path}")


# Приклад використання:
true_crop_pdf(
    input_path="4_kurs_v0.5.pdf",
    output_path="real_cropped_schedule.pdf",
    crop_box=(0, 0, 660, 1200),  # твої координати (x0, y0, x1, y1)
)