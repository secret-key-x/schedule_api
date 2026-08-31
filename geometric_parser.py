import json
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pdfplumber

class PDFGeometricTableParser:
    def __init__(self, pdf_path: str, text_gap_threshold: float = 5.0):
        self.pdf_path = pdf_path
        self.text_gap_threshold = text_gap_threshold
        self.lines = {"horizontal": [], "vertical": []}
        self.intersections = []
        self.cells = []
        self.parsed_pages_cells = []

    def _extract_and_normalize_lines(self, page, thickness_threshold=3.0, min_length=5.0):
        self.lines = {"horizontal": [], "vertical": []}

        for r in page.objects.get("rect", []):
            width = r["x1"] - r["x0"]
            height = r["bottom"] - r["top"]

            if width < thickness_threshold and height >= min_length:
                mid_x = (r["x0"] + r["x1"]) / 2
                self.lines["vertical"].append({
                    "x": mid_x,
                    "top": r["top"],
                    "bottom": r["bottom"],
                })
            elif height < thickness_threshold and width >= min_length:
                mid_y = (r["top"] + r["bottom"]) / 2
                self.lines["horizontal"].append({
                    "y": mid_y,
                    "x0": r["x0"],
                    "x1": r["x1"],
                })

        for l in page.objects.get("line", []):
            w = abs(l["x1"] - l["x0"])
            h = abs(l["bottom"] - l["top"])

            if w < 1.5 and h >= min_length:
                self.lines["vertical"].append({
                    "x": (l["x0"] + l["x1"]) / 2,
                    "top": min(l["top"], l["bottom"]),
                    "bottom": max(l["top"], l["bottom"]),
                })
            elif h < 1.5 and w >= min_length:
                self.lines["horizontal"].append({
                    "y": (l["top"] + l["bottom"]) / 2,
                    "x0": min(l["x0"], l["x1"]),
                    "x1": max(l["x0"], l["x1"]),
                })

        self.lines["vertical"] = self._merge_vertical_lines(self.lines["vertical"])
        self.lines["horizontal"] = self._merge_horizontal_lines(self.lines["horizontal"])

    def _merge_vertical_lines(self, lines: List[dict], x_tolerance: float = 2.0, gap_tolerance: float = 4.0) -> List[dict]:
        if not lines:
            return []

        lines = sorted(lines, key=lambda item: (round(item["x"] / x_tolerance), item["top"]))
        groups = []

        for line in lines:
            placed = False
            for group in groups:
                avg_x = sum(item["x"] for item in group) / len(group)
                if abs(line["x"] - avg_x) <= x_tolerance:
                    group.append(line)
                    placed = True
                    break

            if not placed:
                groups.append([line])

        merged = []
        for group in groups:
            x = sum(item["x"] for item in group) / len(group)
            segments = sorted([(item["top"], item["bottom"]) for item in group])

            cur_top, cur_bottom = segments[0]
            for top, bottom in segments[1:]:
                if top <= cur_bottom + gap_tolerance:
                    cur_bottom = max(cur_bottom, bottom)
                else:
                    merged.append({"x": x, "top": cur_top, "bottom": cur_bottom})
                    cur_top, cur_bottom = top, bottom

            merged.append({"x": x, "top": cur_top, "bottom": cur_bottom})

        return merged

    def _merge_horizontal_lines(self, lines: List[dict], y_tolerance: float = 2.0, gap_tolerance: float = 4.0) -> List[dict]:
        if not lines:
            return []

        lines = sorted(lines, key=lambda item: (round(item["y"] / y_tolerance), item["x0"]))
        groups = []

        for line in lines:
            placed = False
            for group in groups:
                avg_y = sum(item["y"] for item in group) / len(group)
                if abs(line["y"] - avg_y) <= y_tolerance:
                    group.append(line)
                    placed = True
                    break

            if not placed:
                groups.append([line])

        merged = []
        for group in groups:
            y = sum(item["y"] for item in group) / len(group)
            segments = sorted([(item["x0"], item["x1"]) for item in group])

            cur_x0, cur_x1 = segments[0]
            for x0, x1 in segments[1:]:
                if x0 <= cur_x1 + gap_tolerance:
                    cur_x1 = max(cur_x1, x1)
                else:
                    merged.append({"y": y, "x0": cur_x0, "x1": cur_x1})
                    cur_x0, cur_x1 = x0, x1

            merged.append({"y": y, "x0": cur_x0, "x1": cur_x1})

        return merged

    def _heal_table_grid(self, gap_tolerance=10.0):
        if not self.lines["horizontal"] or not self.lines["vertical"]:
            return

        horizontal_groups = self._split_lines_into_table_bands()

        for band in horizontal_groups:
            band_h = band["horizontal"]
            band_v = [
                v for v in self.lines["vertical"]
                if not (v["bottom"] < band["top"] - 5 or v["top"] > band["bottom"] + 5)
            ]

            if not band_h or not band_v:
                continue

            min_y = min(h["y"] for h in band_h)
            max_y = max(h["y"] for h in band_h)
            vertical_xs = sorted(set(round(v["x"], 1) for v in band_v))

            if not vertical_xs:
                continue

            min_x = vertical_xs[0]
            max_x = vertical_xs[-1]

            for v in band_v:
                if abs(v["x"] - min_x) < 5.0 or abs(v["x"] - max_x) < 5.0:
                    v["top"] = min(v["top"], min_y)
                    v["bottom"] = max(v["bottom"], max_y)

                if v["bottom"] > max_y - 200.0:
                    v["bottom"] = max(v["bottom"], max_y)

            for h in band_h:
                if abs(h["y"] - min_y) < 5.0 or abs(h["y"] - max_y) < 5.0:
                    h["x0"] = min(h["x0"], min_x)
                    h["x1"] = max(h["x1"], max_x)

                for vx in vertical_xs:
                    if 0 < (h["x0"] - vx) <= gap_tolerance:
                        h["x0"] = vx
                        break

                for vx in reversed(vertical_xs):
                    if 0 < (vx - h["x1"]) <= gap_tolerance:
                        h["x1"] = vx
                        break

    def _split_lines_into_table_bands(self, max_vertical_gap: float = 45.0) -> List[dict]:
        horizontals = sorted(self.lines["horizontal"], key=lambda h: h["y"])
        if not horizontals:
            return []

        bands = []
        current = [horizontals[0]]

        for h in horizontals[1:]:
            if h["y"] - current[-1]["y"] <= max_vertical_gap:
                current.append(h)
            else:
                bands.append(current)
                current = [h]

        bands.append(current)

        result = []
        for band in bands:
            if len(band) < 2:
                continue

            result.append({
                "top": min(h["y"] for h in band),
                "bottom": max(h["y"] for h in band),
                "horizontal": band,
            })

        return result

    def _find_intersections(self, extend_lines=3.0):
        intersections = set()

        for h in self.lines["horizontal"]:
            for v in self.lines["vertical"]:
                inside_x = (h["x0"] - extend_lines) <= v["x"] <= (h["x1"] + extend_lines)
                inside_y = (v["top"] - extend_lines) <= h["y"] <= (v["bottom"] + extend_lines)

                if inside_x and inside_y:
                    intersections.add((round(v["x"], 1), round(h["y"], 1)))

        self.intersections = sorted(intersections, key=lambda p: (p[1], p[0]))

    def _is_connected(self, x1, y1, x2, y2, tolerance=2.5):
        if abs(x1 - x2) < tolerance:
            ymin, ymax = min(y1, y2), max(y1, y2)

            for v in self.lines["vertical"]:
                if abs(v["x"] - x1) < tolerance:
                    if v["top"] <= ymin + tolerance and v["bottom"] >= ymax - tolerance:
                        return True

        elif abs(y1 - y2) < tolerance:
            xmin, xmax = min(x1, x2), max(x1, x2)

            for h in self.lines["horizontal"]:
                if abs(h["y"] - y1) < tolerance:
                    if h["x0"] <= xmin + tolerance and h["x1"] >= xmax - tolerance:
                        return True

        return False

    def _generate_minimal_cells(self):
        raw_cells = []

        for x1, y1 in self.intersections:
            rights = [p for p in self.intersections if abs(p[1] - y1) < 1.2 and p[0] > x1]
            belows = [p for p in self.intersections if abs(p[0] - x1) < 1.2 and p[1] > y1]

            if not rights or not belows:
                continue

            cell_found = False

            for p_right in sorted(rights, key=lambda p: p[0]):
                x2 = p_right[0]

                if not self._is_connected(x1, y1, x2, y1):
                    continue

                for p_below in sorted(belows, key=lambda p: p[1]):
                    y2 = p_below[1]

                    if not self._is_connected(x1, y1, x1, y2):
                        continue
                    if not self._is_connected(x1, y2, x2, y2):
                        continue
                    if not self._is_connected(x2, y1, x2, y2):
                        continue

                    has_internal = any(
                        x1 < p[0] < x2 and y1 < p[1] < y2
                        for p in self.intersections
                    )

                    if not has_internal:
                        bbox = (round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1))

                        if bbox not in raw_cells:
                            raw_cells.append(bbox)

                        cell_found = True
                        break

                if cell_found:
                    break

        self.cells = []

        for bbox in raw_cells:
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]

            if w >= 15.0 and h >= 8.0:
                self.cells.append(bbox)

    def _split_cells_into_tables(self, cells: List[Tuple[float, float, float, float]]) -> List[List[Tuple[float, float, float, float]]]:
        """
        Розділяє клітинки на незалежні таблиці.
        Це критично для 4 курсу, де на сторінці може бути окрема таблиця для однієї групи.
        """
        if not cells:
            return []

        sorted_cells = sorted(cells, key=lambda b: (b[1], b[0]))
        components = []

        for cell in sorted_cells:
            placed = False

            for component in components:
                if self._cell_belongs_to_component(cell, component):
                    component.append(cell)
                    placed = True
                    break

            if not placed:
                components.append([cell])

        merged = True
        while merged:
            merged = False
            new_components = []

            while components:
                current = components.pop(0)
                i = 0

                while i < len(components):
                    if self._components_touch(current, components[i]):
                        current.extend(components.pop(i))
                        merged = True
                    else:
                        i += 1

                new_components.append(current)

            components = new_components

        components = [
            sorted(component, key=lambda b: (b[1], b[0]))
            for component in components
            if len(component) >= 4
        ]

        components.sort(key=lambda component: (
            min(b[1] for b in component),
            min(b[0] for b in component),
        ))

        return components

    def _cell_belongs_to_component(self, cell, component, tolerance: float = 4.0) -> bool:
        for other in component:
            horizontal_touch = (
                abs(cell[0] - other[2]) <= tolerance or
                abs(cell[2] - other[0]) <= tolerance or
                self._ranges_overlap(cell[0], cell[2], other[0], other[2], min_overlap=5.0)
            )
            vertical_touch = (
                abs(cell[1] - other[3]) <= tolerance or
                abs(cell[3] - other[1]) <= tolerance or
                self._ranges_overlap(cell[1], cell[3], other[1], other[3], min_overlap=5.0)
            )

            if horizontal_touch and vertical_touch:
                return True

        return False

    def _components_touch(self, first, second, tolerance: float = 8.0) -> bool:
        f_box = self._component_bbox(first)
        s_box = self._component_bbox(second)

        x_close = not (f_box[2] < s_box[0] - tolerance or s_box[2] < f_box[0] - tolerance)
        y_close = not (f_box[3] < s_box[1] - tolerance or s_box[3] < f_box[1] - tolerance)

        return x_close and y_close

    def _component_bbox(self, component):
        return (
            min(b[0] for b in component),
            min(b[1] for b in component),
            max(b[2] for b in component),
            max(b[3] for b in component),
        )

    def _ranges_overlap(self, a0, a1, b0, b1, min_overlap: float = 1.0) -> bool:
        return min(a1, b1) - max(a0, b0) >= min_overlap

    def _extract_custom_text(self, page, bbox, is_first_col=False):
        cropped = page.crop(bbox, strict=False)
        raw_chars = [c for c in cropped.chars if c["width"] > 0 and c["height"] > 2]

        chars = []

        for c in raw_chars:
            color = c.get("non_stroking_color")
            if color in [(1, 1, 1), [1, 1, 1], (1,), 1, "1", 1.0, (1.0, 1.0, 1.0)]:
                continue

            is_colliding = False

            for i, prev_c in enumerate(chars):
                x_overlap = max(0, min(c["x1"], prev_c["x1"]) - max(c["x0"], prev_c["x0"]))
                y_overlap = max(0, min(c["bottom"], prev_c["bottom"]) - max(c["top"], prev_c["top"]))
                char_area = (c["x1"] - c["x0"]) * (c["bottom"] - c["top"])

                if char_area > 0 and (x_overlap * y_overlap) / char_area > 0.4:
                    chars[i] = c
                    is_colliding = True
                    break

            if not is_colliding:
                chars.append(c)

        if not chars:
            return ""

        if is_first_col:
            chars.sort(key=lambda c: c["bottom"], reverse=True)
            return "".join(c["text"] for c in chars).strip()

        mid_y = (bbox[1] + bbox[3]) / 2
        chars.sort(key=lambda c: c["top"])

        lines = []

        for c in chars:
            placed = False

            for line in lines:
                line_top = sum(char["top"] for char in line) / len(line)
                line_bottom = sum(char["bottom"] for char in line) / len(line)
                overlap = max(0, min(c["bottom"], line_bottom) - max(c["top"], line_top))
                char_h = c["bottom"] - c["top"]
                line_h = line_bottom - line_top

                if char_h > 0 and line_h > 0 and (overlap / min(char_h, line_h)) > 0.4:
                    line.append(c)
                    placed = True
                    break

            if not placed:
                lines.append([c])

        lines.sort(key=lambda l: sum(c["top"] for c in l) / len(l))

        for line in lines:
            line.sort(key=lambda c: c["x0"])

        top_lines, bottom_lines = [], []

        for line in lines:
            line_mid_y = sum((c["top"] + c["bottom"]) / 2 for c in line) / len(line)

            if line_mid_y < mid_y:
                top_lines.append(line)
            else:
                bottom_lines.append(line)

        def process_block(block_lines):
            if not block_lines:
                return None

            right_threshold = bbox[0] + (bbox[2] - bbox[0]) * 0.60
            right_starts = []

            for line in block_lines:
                if not line:
                    continue

                word_start = line[0]["x0"]

                for i in range(1, len(line)):
                    if line[i]["x0"] - line[i - 1]["x1"] > self.text_gap_threshold:
                        if word_start > right_threshold:
                            right_starts.append(word_start)

                        word_start = line[i]["x0"]

                if word_start > right_threshold:
                    right_starts.append(word_start)

            split_x = min(right_starts) - 2 if right_starts else bbox[2] + 999
            left_lines, right_lines = [], []

            for line in block_lines:
                words, cur_word = [], [line[0]]

                for c in line[1:]:
                    if c["x0"] - cur_word[-1]["x1"] > self.text_gap_threshold:
                        words.append(cur_word)
                        cur_word = [c]
                    else:
                        cur_word.append(c)

                words.append(cur_word)

                l_parts, r_parts = [], []

                for word in words:
                    w_text = "".join(c["text"] for c in word).strip()

                    if not w_text:
                        continue

                    if word[0]["x0"] < split_x:
                        l_parts.append(w_text)
                    else:
                        r_parts.append(w_text)

                if l_parts:
                    left_lines.append(" ".join(l_parts))
                if r_parts:
                    right_lines.append(" ".join(r_parts))

            left_text = ""

            for chunk in left_lines:
                if left_text:
                    left_text += chunk if left_text.endswith("-") else " " + chunk
                else:
                    left_text = chunk

            right_text = " ".join(right_lines).strip()

            return f"{left_text} | {right_text}" if right_text else left_text

        res = []

        top_text = process_block(top_lines)
        if top_text:
            res.append(top_text)

        bottom_text = process_block(bottom_lines)
        if bottom_text:
            res.append(bottom_text)

        return "\n".join(res)

    def parse_table_geometric(self, pages: list = None) -> list:
        self.parsed_pages_cells = []

        with pdfplumber.open(self.pdf_path) as pdf:
            pages_to_process = pages if pages is not None else range(len(pdf.pages))

            for page_index in pages_to_process:
                page = pdf.pages[page_index]

                self._extract_and_normalize_lines(page)
                self._heal_table_grid()
                self._find_intersections()
                self._generate_minimal_cells()

                if not self.cells:
                    continue

                table_components = self._split_cells_into_tables(self.cells)

                for table_id, table_cells in enumerate(table_components):
                    page_cols_x = self._build_local_columns(table_cells)

                    for coords in table_cells:
                        x0 = coords[0]
                        col_idx = self._resolve_col_idx(x0, page_cols_x)

                        if col_idx == -1:
                            continue

                        text = self._extract_custom_text(
                            page,
                            coords,
                            is_first_col=(col_idx == 0),
                        )

                        self.parsed_pages_cells.append({
                            "page": page_index,
                            "table_id": table_id,
                            "bbox": coords,
                            "text": text.strip() if text else "",
                            "col_idx": col_idx,
                        })

        return self.parsed_pages_cells

    def _build_local_columns(self, table_cells: List[Tuple[float, float, float, float]]) -> List[float]:
        starts = sorted(b[0] for b in table_cells)
        columns = []

        for x in starts:
            if not columns or abs(x - columns[-1]) > 12:
                columns.append(x)
            else:
                columns[-1] = (columns[-1] + x) / 2

        return columns

    def _resolve_col_idx(self, x0: float, columns: List[float]) -> int:
        if not columns:
            return -1

        closest_idx = min(range(len(columns)), key=lambda i: abs(x0 - columns[i]))

        if abs(x0 - columns[closest_idx]) <= 16:
            return closest_idx

        return -1

    def export_schedule_to_json(self, output_path: str = None) -> dict:
        if not self.parsed_pages_cells:
            return {}

        schedule_json = {}
        max_page = max(c["page"] for c in self.parsed_pages_cells)

        day_mapping = {
            "понеділок": "Понеділок",
            "вівторок": "Вівторок",
            "середа": "Середа",
            "четвер": "Четвер",
            "п'ятниця": "П'ятниця",
            "пятниця": "П'ятниця",
            "субота": "Субота",
            "неділя": "Неділя",
        }

        raw_day_patterns = {
            "понеділок": ["п", "о", "н", "е", "д", "і", "л", "о", "к"],
            "вівторок": ["в", "і", "в", "т", "о", "р", "о", "к"],
            "середа": ["с", "е", "р", "е", "д", "а"],
            "четвер": ["ч", "е", "т", "в", "е", "р"],
            "п'ятниця": ["п", "я", "т", "н", "и", "ц", "я"],
            "субота": ["с", "у", "б", "о", "т", "а"],
        }

        ignore_phrases = [
            "декан",
            "львівський",
            "розклад",
            "затверджую",
            "проректор",
            "семестр",
            "курс",
            "факультет",
            "університет",
        ]

        garbage_words = [
            "пара",
            "час",
            "години",
            "день",
            "дні",
            "група",
            "підгрупа",
        ]

        active_day_by_table = {}
        last_header_by_column_count = {}

        with pdfplumber.open(self.pdf_path) as pdf:
            for p in range(max_page + 1):
                page = pdf.pages[p]
                page_cells = [c for c in self.parsed_pages_cells if c["page"] == p]
                if not page_cells:
                    continue

                table_ids = sorted(set(c.get("table_id", 0) for c in page_cells))

                for table_id in table_ids:
                    table_cells = [
                        c for c in page_cells
                        if c.get("table_id", 0) == table_id
                    ]

                    if not table_cells:
                        continue

                    table_cells.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))

                    header_names, schedule_start_y, header_bboxes = self._detect_group_headers(table_cells, page=page)
                    column_count = max(c["col_idx"] for c in table_cells) + 1

                    if header_names:
                        last_header_by_column_count[column_count] = (header_names, schedule_start_y, header_bboxes)
                    else:
                        header_names, schedule_start_y, header_bboxes = last_header_by_column_count.get(column_count, ({}, 0, []))

                    if not header_names:
                        continue

                    for group in header_names.values():
                        schedule_json.setdefault(group, {})

                    table_key = (p, table_id)
                    active_day = active_day_by_table.get(table_key, "Невідомий день")

                    day_anchors = self._detect_day_anchors(
                        table_cells=table_cells,
                        day_mapping=day_mapping,
                        raw_day_patterns=raw_day_patterns,
                    )

                    if active_day == "Невідомий день" and day_anchors:
                        active_day = day_anchors[0]["day"]

                    time_cells = self._detect_time_cells(
                        table_cells=table_cells,
                        garbage_words=garbage_words,
                    )

                    if not time_cells:
                        continue

                    time_cells = self._normalize_time_cells(time_cells)

                    data_cells = [
                        c for c in table_cells
                        if c["col_idx"] in header_names
                    ]
                    data_cells.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))

                    for cell in data_cells:
                        txt = cell["text"].strip()

                        if not txt:
                            continue

                        if any(phrase in txt.lower() for phrase in ignore_phrases):
                            continue
                        
                        # Перевіряємо чи це не сама клітинка заголовка (всі 4 координати мають збігатися)
                        if any(all(abs(cell["bbox"][i] - hb[i]) < 0.5 for i in range(4)) for hb in header_bboxes):
                            continue
                            
                        if cell["bbox"][1] < schedule_start_y - 15: # Більш ліберальний поріг
                            continue

                        y0, y1 = cell["bbox"][1], cell["bbox"][3]
                        y_mid = (y0 + y1) / 2

                        current_cell_day = active_day

                        for anchor in day_anchors:
                            if y_mid >= anchor["y0"] - 2:
                                current_cell_day = anchor["day"]

                        active_day = current_cell_day

                        target_time_cell = self._find_time_cell_for_bbox(cell["bbox"], time_cells)
                        
                        if not target_time_cell:
                            continue

                        time_text = target_time_cell["normalized_text"]

                        t_y0, t_y1 = target_time_cell["bbox"][1], target_time_cell["bbox"][3]
                        t_h = t_y1 - t_y0
                        t_mid = (t_y0 + t_y1) / 2
                        cell_h = y1 - y0

                        if cell_h >= (t_h * 0.8):
                            position = "mono"
                        else:
                            position = "top" if y_mid < t_mid else "bottom"

                        group_name = header_names[cell["col_idx"]]

                        schedule_json.setdefault(group_name, {})
                        schedule_json[group_name].setdefault(active_day, {})
                        schedule_json[group_name][active_day].setdefault(time_text, [])

                        schedule_json[group_name][active_day][time_text].append({
                            "position": position,
                            "text": cell["text"],
                        })

                    active_day_by_table[table_key] = active_day

        schedule_json = self._deduplicate_schedule(schedule_json)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(schedule_json, f, ensure_ascii=False, indent=4)

        return schedule_json

    def _detect_group_headers(self, table_cells: List[dict], page=None) -> Tuple[Dict[int, str], float, List[tuple]]:
        """
        Визначає заголовки академгруп та повертає список bboxes заголовків.
        """
        candidates = []

        for cell in table_cells:
            text = self._normalize_text(cell.get("text", ""))
            if not text:
                continue

            group_name = self._extract_group_name(text)
            if group_name:
                candidates.append({
                    "group": group_name,
                    "bbox": cell["bbox"],
                    "col_idx": cell["col_idx"]
                })

        # Якщо в клітинках не знайшли назви груп, спробуємо пошукати просто в тексті сторінки
        if not candidates and page:
            words = page.extract_words()
            # Визначаємо межі наявної таблиці
            t_x0 = min(c["bbox"][0] for c in table_cells)
            t_x1 = max(c["bbox"][2] for c in table_cells)
            t_y0 = min(c["bbox"][1] for c in table_cells)
            
            # Шукаємо слова, що схожі на групи, над таблицею або у верхній її частині
            for w in words:
                text = self._normalize_text(w["text"])
                group_name = self._extract_group_name(text)
                if group_name:
                    # Перевіряємо чи слово знаходиться по горизонталі в межах таблиці
                    if t_x0 - 20 <= w["x0"] <= t_x1 + 20 and w["bottom"] <= t_y0 + 50:
                        # Визначаємо колонку
                        cols_x = self._build_local_columns([c["bbox"] for c in table_cells])
                        idx = self._resolve_col_idx(w["x0"], cols_x)
                        if idx != -1:
                            candidates.append({
                                "group": group_name,
                                "bbox": (w["x0"], w["top"], w["x1"], w["bottom"]),
                                "col_idx": idx
                            })

        header_names = {}
        header_bboxes = []
        schedule_start_y = 0

        if candidates:
            # Групуємо кандидати по рядах і беремо найвищий ряд, де є хоча б один кандидат
            rows = self._group_cells_by_rows(candidates)
            if rows:
                header_row = rows[0]
                for c in header_row:
                    header_names[c["col_idx"]] = c["group"]
                    header_bboxes.append(c["bbox"])
                    schedule_start_y = max(schedule_start_y, c["bbox"][3])
                
                return header_names, schedule_start_y, header_bboxes

        # Fallback
        rows = self._group_cells_by_rows(table_cells)
        best_row = None
        best_score = 0

        for row in rows:
            cells = [c for c in row if c["col_idx"] >= 1 and self._normalize_text(c.get("text", ""))]
            score = 0
            for cell in cells:
                text = self._normalize_text(cell["text"])
                if 2 <= len(text) <= 40 and not self._looks_like_lesson_text(text):
                    score += 1

            if score > best_score:
                best_score = score
                best_row = row

        if best_row and best_score > 0:
            for cell in best_row:
                text = self._normalize_text(cell.get("text", ""))
                if not text or self._looks_like_lesson_text(text):
                    continue
                header_names[cell["col_idx"]] = text
                header_bboxes.append(cell["bbox"])

            schedule_start_y = max(c["bbox"][3] for c in best_row)

        return header_names, schedule_start_y, header_bboxes

    def _extract_group_name(self, text: str) -> Optional[str]:
        # Виключаємо суто римські цифри та час, які можуть помилково потрапити сюди
        if re.match(r"^(VIII|VII|VI|IV|V|III|II|I)\s*(\d{2,3})?$", text, flags=re.IGNORECASE):
            return None
        if re.match(r"^\d{1,2}[:.]\d{2}", text):
            return None

        # Мапінг для "битих" символів, які часто зустрічаються в цих PDF
        # Використовуємо коди символів безпосередньо
        char_map = {
            0x041c: "М", # М
            0x041a: "К", # К
            0x0410: "А", # А
            0x0422: "Т", # Т
            0x041f: "П", # П
            0x0421: "С", # С
            0x0420: "Р", # Р
            0x0418: "И",
            0x041d: "Н",
            0x0415: "Е",
            0x0424: "Ф",
            0x041c: "М", 
            0x0422: "Т", 
            0x041a: "К", 
            0x0410: "А", 
            0x041e: "О",
            0x041c: "М",
        }
        
        # Спробуємо замінити символи
        clean_text = ""
        for char in text:
            code = ord(char)
            if code in char_map:
                clean_text += char_map[code]
            elif 0x0400 <= code <= 0x04FF: # Залишаємо кирилицю як є
                clean_text += char
            elif 0x0020 <= code <= 0x007E: # Залишаємо ASCII як є
                clean_text += char
            elif code == 0x00A0: # Non-breaking space
                clean_text += " "
            else:
                # Спробуємо мапити зміщені коди (наприклад 0x1c04 -> 0x041c)
                # Багато систем міняють місцями байти або зміщують коди
                swapped = ((code & 0xFF) << 8) | (code >> 8)
                if swapped in char_map:
                    clean_text += char_map[swapped]
                elif 0x0400 <= swapped <= 0x04FF:
                    clean_text += chr(swapped)
                else:
                    clean_text += char

        # Спеціальна логіка для магістрів та випадків з поганим кодуванням.
        patterns = [
            # Стандартні групи: МТА-11, МТП-41
            r"([А-ЯІЇЄҐA-Z]{2,6})\s*[-–]\s*(\d{2,3}[А-ЯІЇЄҐA-Z]?)\b",
            # Магістри: МТАМ-11, МТПМ-11
            r"([А-ЯІЇЄҐA-Z]{2,6})\s*[-–]?\s*[мМ]\s*[-–]?\s*(\d{1,2}[А-ЯІЇЄҐA-Z]?)\b",
            # Варіант з пробілом замість дефіса
            r"([А-ЯІЇЄҐA-Z]{2,6})\s+(\d{2,3}[А-ЯІЇЄҐA-Z]?)\b",
            # Дуже ліберальний пошук для "битих" PDF
            r"([А-ЯІЇЄҐA-Z]{1,6})\s*[-–]?\s*(\d{2,3}[А-ЯІЇЄҐA-Z]?)\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, clean_text)
            if match:
                g1 = match.group(1).upper()
                g2 = match.group(2)
                
                if g1 in ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]:
                    continue
                
                # Якщо це магістерська група (є префікс + М)
                if "М" in pattern and "м" in match.group(0).lower():
                    return f"{g1}М-{g2}"
                
                return f"{g1}-{g2}"

        return None

    def _looks_like_lesson_text(self, text: str) -> bool:
        lowered = text.lower()

        lesson_markers = [
            "лек",
            "пр",
            "практ",
            "лаб",
            "ауд",
            "доц",
            "проф",
            "асист",
            "каф",
            "дисципл",
            "технолог",
            "систем",
            "метод",
            "аналіз",
            "проект",
            "модел",
            "управл",
        ]

        if any(marker in lowered for marker in lesson_markers):
            return True

        if re.search(r"\d{1,2}:\d{2}", text):
            return True

        return len(text) > 45

    def _group_cells_by_rows(self, cells: List[dict], tolerance: float = 4.0) -> List[List[dict]]:
        rows = []

        for cell in sorted(cells, key=lambda c: c["bbox"][1]):
            placed = False
            y0 = cell["bbox"][1]

            for row in rows:
                row_y = sum(c["bbox"][1] for c in row) / len(row)

                if abs(y0 - row_y) <= tolerance:
                    row.append(cell)
                    placed = True
                    break

            if not placed:
                rows.append([cell])

        for row in rows:
            row.sort(key=lambda c: c["bbox"][0])

        rows.sort(key=lambda row: min(c["bbox"][1] for c in row))

        return rows

    def _detect_day_anchors(self, table_cells, day_mapping, raw_day_patterns):
        day_anchors = []

        for cell in table_cells:
            # Не обмежуємо лише 0-ю колонкою
            txt = cell.get("text", "")
            if not txt or len(txt) > 20:
                continue

            cln_text = self._normalize_text(txt).lower()
            compact = re.sub(r"[^а-яіїєґa-z']", "", cln_text)
            matched_day = None

            for key, val in day_mapping.items():
                if key in compact or key in cln_text:
                    matched_day = val
                    break

            if not matched_day:
                for day_name, pattern in raw_day_patterns.items():
                    match_count = sum(1 for char in pattern if char in compact)

                    if match_count >= len(pattern) - 1:
                        matched_day = day_mapping[day_name]
                        break

            if matched_day:
                day_anchors.append({
                    "day": matched_day,
                    "y0": cell["bbox"][1],
                    "y1": cell["bbox"][3],
                })

        day_anchors.sort(key=lambda x: x["y0"])
        return day_anchors

    def _detect_time_cells(self, table_cells, garbage_words):
        time_cells = []

        for cell in table_cells:
            # Не обмежуємо лише 1-ю колонкою
            txt = self._normalize_text(cell.get("text", ""))

            if not txt or len(txt) > 30:
                continue

            lowered = txt.lower()

            if any(gw in lowered for gw in garbage_words):
                continue

            if self._looks_like_time_cell(txt):
                time_cells.append(cell)

        time_cells.sort(key=lambda c: c["bbox"][1])
        return time_cells

    def _looks_like_time_cell(self, text: str) -> bool:
        text = self._normalize_text(text)

        if re.search(r"\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2}", text):
            return True

        if re.match(r"^(VIII|VII|VI|IV|V|III|II|I|\d{1,2})\b", text, flags=re.IGNORECASE):
            return True

        return False

    def _normalize_time_cells(self, time_cells: List[dict]) -> List[dict]:
        roman_by_index = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]

        normalized = []

        for i, cell in enumerate(time_cells):
            text = self._normalize_text(cell["text"])
            text = text.replace("–", "-").replace(".", ":")

            lesson_match = re.match(r"^(VIII|VII|VI|IV|V|III|II|I|\d{1,2})\b", text, flags=re.IGNORECASE)
            time_match = re.search(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", text)

            lesson_part = ""
            if lesson_match:
                lesson_part = lesson_match.group(1).upper()

            if not lesson_part and i < len(roman_by_index):
                lesson_part = roman_by_index[i]

            if time_match:
                time_part = re.sub(r"\s+", "", time_match.group(0))
                normalized_text = f"{lesson_part} {time_part}".strip()
            else:
                normalized_text = lesson_part or text

            new_cell = dict(cell)
            new_cell["normalized_text"] = normalized_text
            normalized.append(new_cell)

        return normalized

    def _find_time_cell_for_bbox(self, bbox, time_cells):
        y0, y1 = bbox[1], bbox[3]
        x0 = bbox[0]
        
        candidates = [tc for tc in time_cells if tc["bbox"][0] <= x0]
        if not candidates:
            candidates = time_cells

        best_cell = None
        max_score = -1.0

        for tc in candidates:
            overlap = min(y1, tc["bbox"][3]) - max(y0, tc["bbox"][1])
            if overlap <= 0:
                continue

            dist_x = x0 - tc["bbox"][2]
            # Скор базується на перекритті по вертикалі та близькості по горизонталі
            score = overlap / (1 + max(0, dist_x) * 0.05)

            if score > max_score:
                max_score = score
                best_cell = tc

        return best_cell

    def _normalize_text(self, text: str) -> str:
        text = text or ""
        text = text.replace("\n", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _deduplicate_schedule(self, schedule_json: dict) -> dict:
        for group in schedule_json:
            for day in schedule_json[group]:
                for time_slot, classes in schedule_json[group][day].items():
                    unique_classes = []
                    seen = set()

                    for c in classes:
                        identifier = (
                            c.get("position"),
                            self._normalize_text(c.get("text", "")),
                        )

                        if identifier not in seen:
                            seen.add(identifier)
                            unique_classes.append(c)

                    schedule_json[group][day][time_slot] = unique_classes

        return schedule_json

    def visualize_debug(self, page_index: int = 0, output_image_path: str = "debug_vision.png"):
        with pdfplumber.open(self.pdf_path) as pdf:
            if page_index >= len(pdf.pages):
                return

            page = pdf.pages[page_index]
            self.lines = {"horizontal": [], "vertical": []}
            self.intersections = []
            self.cells = []

            self._extract_and_normalize_lines(page)
            self._heal_table_grid()
            self._find_intersections()
            self._generate_minimal_cells()

            im = page.to_image(resolution=150)

            if self.cells:
                im.draw_rects(self.cells, stroke="red", stroke_width=2, fill=None)

            if self.intersections:
                im.draw_circles(self.intersections, radius=3, stroke="green", fill="green")

            im.save(output_image_path)


if __name__ == "__main__":
    parser = PDFGeometricTableParser("real_cropped_schedule.pdf", text_gap_threshold=5.0)
    parser.parse_table_geometric()
    parser.visualize_debug(page_index=0, output_image_path="debug_page_0.png")
    parser.export_schedule_to_json("output_schedule.json")