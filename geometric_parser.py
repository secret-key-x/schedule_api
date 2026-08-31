import json
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
                self.lines["vertical"].append({"x": mid_x, "top": r["top"], "bottom": r["bottom"]})
            elif height < thickness_threshold and width >= min_length:
                mid_y = (r["top"] + r["bottom"]) / 2
                self.lines["horizontal"].append({"y": mid_y, "x0": r["x0"], "x1": r["x1"]})

        for l in page.objects.get("line", []):
            w = abs(l["x1"] - l["x0"])
            h = abs(l["bottom"] - l["top"])
            if w < 1.0 and h >= min_length:
                self.lines["vertical"].append({
                    "x": l["x0"],
                    "top": min(l["top"], l["bottom"]),
                    "bottom": max(l["top"], l["bottom"]),
                })
            elif h < 1.0 and w >= min_length:
                self.lines["horizontal"].append({
                    "y": l["top"],
                    "x0": min(l["x0"], l["x1"]),
                    "x1": max(l["x0"], l["x1"]),
                })

    def _heal_table_grid(self, gap_tolerance=10.0):
        """
        Відкат агресивної логіки. Замикаємо лише ЗОВНІШНІЙ контур таблиці.
        """
        if not self.lines["horizontal"] or not self.lines["vertical"]:
            return

        min_y = min(h["y"] for h in self.lines["horizontal"])
        max_y = max(h["y"] for h in self.lines["horizontal"])

        vertical_xs = sorted(list(set(round(v["x"], 1) for v in self.lines["vertical"])))
        if not vertical_xs:
            return

        min_x = vertical_xs[0]
        max_x = vertical_xs[-1]

        # 1. ЗАМИКАННЯ ЗОВНІШНЬОГО КОНТУРУ
        # Крайню ліву та крайню праву вертикальні лінії тягнемо від самого верху до самого низу таблиці
        for v in self.lines["vertical"]:
            if abs(v["x"] - min_x) < 5.0 or abs(v["x"] - max_x) < 5.0:
                v["top"] = min_y
                v["bottom"] = max_y

        # Найвищу та найнижчу горизонтальні лінії тягнемо від крайнього лівого до крайнього правого боку
        for h in self.lines["horizontal"]:
            if abs(h["y"] - min_y) < 5.0 or abs(h["y"] - max_y) < 5.0:
                h["x0"] = min_x
                h["x1"] = max_x

        # 2. М'яке дотягування внутрішніх ліній (без перетину комірок днів)
        for h in self.lines["horizontal"]:
            for vx in vertical_xs:
                if 0 < (h["x0"] - vx) <= gap_tolerance:
                    h["x0"] = vx
                    break
            for vx in reversed(vertical_xs):
                if 0 < (vx - h["x1"]) <= gap_tolerance:
                    h["x1"] = vx
                    break

    def _find_intersections(self, extend_lines=3.0):
        intersections = set()
        for h in self.lines["horizontal"]:
            for v in self.lines["vertical"]:
                inside_x = (h["x0"] - extend_lines) <= v["x"] <= (h["x1"] + extend_lines)
                inside_y = (v["top"] - extend_lines) <= h["y"] <= (v["bottom"] + extend_lines)
                if inside_x and inside_y:
                    intersections.add((round(v["x"], 1), round(h["y"], 1)))

        self.intersections = sorted(list(intersections), key=lambda p: (p[1], p[0]))

    def _is_connected(self, x1, y1, x2, y2, tolerance=2.0):
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
                    if not self._is_connected(x1, y1, x1, y2): continue
                    if not self._is_connected(x1, y2, x2, y2): continue
                    if not self._is_connected(x2, y1, x2, y2): continue

                    has_internal = any(x1 < p[0] < x2 and y1 < p[1] < y2 for p in self.intersections)
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
            return "".join([c["text"] for c in chars]).strip()

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
                if char_h > 0 and (overlap / min(char_h, line_h)) > 0.4:
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
            if not block_lines: return None
            right_threshold = bbox[0] + (bbox[2] - bbox[0]) * 0.60
            right_starts = []

            for line in block_lines:
                if not line: continue
                word_start = line[0]["x0"]
                for i in range(1, len(line)):
                    if line[i]["x0"] - line[i - 1]["x1"] > self.text_gap_threshold:
                        if word_start > right_threshold: right_starts.append(word_start)
                        word_start = line[i]["x0"]
                if word_start > right_threshold: right_starts.append(word_start)

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
                    if word[0]["x0"] < split_x: l_parts.append(w_text)
                    else: r_parts.append(w_text)

                if l_parts: left_lines.append(" ".join(l_parts))
                if r_parts: right_lines.append(" ".join(r_parts))

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
        if top_text: res.append(top_text)
        bottom_text = process_block(bottom_lines)
        if bottom_text: res.append(bottom_text)
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

                page_cols_x = sorted(list(set(round(bbox[0], -1) for bbox in self.cells)))

                for coords in self.cells:
                    x0 = coords[0]
                    col_idx = -1
                    closest_idx = min(range(len(page_cols_x)), key=lambda i: abs(x0 - page_cols_x[i]))
                    
                    if abs(x0 - page_cols_x[closest_idx]) < 15:
                        col_idx = closest_idx

                    if col_idx == -1:
                        continue

                    text = self._extract_custom_text(page, coords, is_first_col=(col_idx == 0))

                    self.parsed_pages_cells.append({
                        "page": page_index,
                        "bbox": coords,
                        "text": text.strip() if text else "",
                        "col_idx": col_idx,
                    })

        return self.parsed_pages_cells

    def export_schedule_to_json(self, output_path: str = None) -> dict:
        if not self.parsed_pages_cells:
            return {}

        groups_headers = {}
        p0_cells = [c for c in self.parsed_pages_cells if c["page"] == 0]

        for cell in p0_cells:
            idx = cell["col_idx"]
            if idx >= 2 and cell["text"]:
                if idx not in groups_headers:
                    groups_headers[idx] = []
                groups_headers[idx].append(cell)

        header_names = {}
        schedule_start_y_p0 = 0
        for idx, cands in groups_headers.items():
            cands.sort(key=lambda c: c["bbox"][1])
            header_names[idx] = cands[0]["text"].replace("\n", " ").strip()
            if cands[0]["bbox"][3] > schedule_start_y_p0:
                schedule_start_y_p0 = cands[0]["bbox"][3]

        ignore_phrases = ["декан", "львівський", "розклад", "затверджую", "проректор", "семестр", "курс"]
        garbage_words = ["пара", "час", "години", "день", "дні", "група", "підгрупа"]

        schedule_json = {name: {} for name in header_names.values()}
        active_day = "Невідомий день"
        max_page = max(c["page"] for c in self.parsed_pages_cells)

        for p in range(max_page + 1):
            page_cells = [c for c in self.parsed_pages_cells if c["page"] == p]
            if not page_cells: continue

            valid_cells = []
            for cell in page_cells:
                txt_lower = cell["text"].lower()
                if any(phrase in txt_lower for phrase in ignore_phrases):
                    continue
                if p == 0 and cell["bbox"][1] < schedule_start_y_p0 - 2:
                    continue
                valid_cells.append(cell)

            rows_dict = {}
            for cell in valid_cells:
                y_mid = round((cell["bbox"][1] + cell["bbox"][3]) / 2, -1)
                if y_mid not in rows_dict:
                    rows_dict[y_mid] = []
                rows_dict[y_mid].append(cell)

            sorted_y_keys = sorted(rows_dict.keys())

            for y_key in sorted_y_keys:
                row = rows_dict[y_key]

                day_cell = next((c for c in row if c["col_idx"] == 0), None)
                if day_cell and day_cell["text"]:
                    cln_day = day_cell["text"].replace("\n", " ").strip()
                    if not any(gw in cln_day.lower() for gw in garbage_words):
                        active_day = cln_day 

                time_cell = next((c for c in row if c["col_idx"] == 1), None)
                if not time_cell or not time_cell["text"]: continue

                time_text = time_cell["text"].replace("\n", " ").strip()
                if any(gw in time_text.lower() for gw in garbage_words) or (len(time_text) <= 1 and time_text.isdigit()):
                    continue

                t_y0, t_y1 = time_cell["bbox"][1], time_cell["bbox"][3]
                t_h = t_y1 - t_y0
                t_mid = (t_y0 + t_y1) / 2

                for data_cell in [c for c in row if c["col_idx"] >= 2]:
                    idx = data_cell["col_idx"]
                    if idx not in header_names: continue

                    grp_name = header_names[idx]
                    cell_h = data_cell["bbox"][3] - data_cell["bbox"][1]
                    cy_mid = (data_cell["bbox"][1] + data_cell["bbox"][3]) / 2

                    if cell_h >= (t_h * 0.8):
                        position = "mono"
                    else:
                        position = "top" if cy_mid < t_mid else "bottom"

                    if active_day not in schedule_json[grp_name]:
                        schedule_json[grp_name][active_day] = {}
                    if time_text not in schedule_json[grp_name][active_day]:
                        schedule_json[grp_name][active_day][time_text] = []

                    schedule_json[grp_name][active_day][time_text].append({
                        "position": position, 
                        "text": data_cell["text"]
                    })

        for grp in schedule_json:
            for day in schedule_json[grp]:
                for time_slot, classes in schedule_json[grp][day].items():
                    unique_classes = []
                    seen = set()
                    has_real_text = any(c["text"].strip() != "" for c in classes)

                    for c in classes:
                        if has_real_text and c["text"].strip() == "":
                            continue
                        identifier = (c["position"], c["text"])
                        if identifier not in seen:
                            seen.add(identifier)
                            unique_classes.append(c)

                    schedule_json[grp][day][time_slot] = unique_classes

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(schedule_json, f, ensure_ascii=False, indent=4)
            print(f"✅ Структурований розклад збережено у файл: {output_path}")

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
            print(f"👁️ Рентген-візуалізацію збережено у файл: {output_image_path}")

if __name__ == "__main__":
    parser = PDFGeometricTableParser(r"4_kurs_v0.3.pdf", text_gap_threshold=5.0)
    parser.parse_table_geometric()
    parser.visualize_debug(page_index=0, output_image_path="debug_page_0.png")
    parser.export_schedule_to_json("output_schedule.json")