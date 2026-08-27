import pdfplumber
import json
import os

class PDFGeometricTableParser:
    def __init__(self, pdf_path: str, text_gap_threshold: float = 2.0):
        self.pdf_path = pdf_path
        self.text_gap_threshold = text_gap_threshold
        self.lines = {"horizontal": [], "vertical": []}
        self.intersections = []
        self.nodes = {}
        self.cells = []
        self.rect_text_dict = {}

        self.columns_x = []
        self.rows_y = []

    def _extract_and_normalize_lines(self, page, thickness_threshold=3.0, min_length=5.0):
        self.lines = {"horizontal": [], "vertical": []}
        for r in page.objects.get("rect", []):
            width = r['x1'] - r['x0']
            height = r['bottom'] - r['top']
            if width < thickness_threshold and height >= min_length:
                mid_x = (r['x0'] + r['x1']) / 2
                self.lines["vertical"].append({"x": mid_x, "top": r['top'], "bottom": r['bottom']})
            elif height < thickness_threshold and width >= min_length:
                mid_y = (r['top'] + r['bottom']) / 2
                self.lines["horizontal"].append({"y": mid_y, "x0": r['x0'], "x1": r['x1']})

        for l in page.objects.get("line", []):
            w = abs(l['x1'] - l['x0'])
            h = abs(l['bottom'] - l['top'])
            if w < 1.0 and h >= min_length:
                self.lines["vertical"].append({"x": l['x0'], "top": min(l['top'], l['bottom']), "bottom": max(l['top'], l['bottom'])})
            elif h < 1.0 and w >= min_length:
                self.lines["horizontal"].append({"y": l['top'], "x0": min(l['x0'], l['x1']), "x1": max(l['x0'], l['x1'])})

    def _heal_table_grid(self, gap_tolerance=8.0):
        if not self.lines["horizontal"] or not self.lines["vertical"]:
            return

        min_y = min(h["y"] for h in self.lines["horizontal"])
        max_y = max(h["y"] for h in self.lines["horizontal"])
        
        vertical_xs = sorted(list(set(round(v["x"], 1) for v in self.lines["vertical"])))
        if not vertical_xs: return
        
        critical_xs = []
        if len(vertical_xs) > 0: critical_xs.append(vertical_xs[0])
        if len(vertical_xs) > 1: critical_xs.append(vertical_xs[1])
        if len(vertical_xs) > 2: critical_xs.append(vertical_xs[2])
        if len(vertical_xs) > 3: critical_xs.append(vertical_xs[-1])

        for v in self.lines["vertical"]:
            if any(abs(v["x"] - cx) < 3.0 for cx in critical_xs):
                v["top"] = min_y
                v["bottom"] = max_y

        min_x, max_x = vertical_xs[0], vertical_xs[-1]

        for h in self.lines["horizontal"]:
            for vx in vertical_xs:
                if 0 < (h["x0"] - vx) <= gap_tolerance:
                    h["x0"] = vx
                    break
            for vx in reversed(vertical_xs):
                if 0 < (vx - h["x1"]) <= gap_tolerance:
                    h["x1"] = vx
                    break
            
            if h["x0"] - min_x < gap_tolerance: h["x0"] = min_x
            if max_x - h["x1"] < gap_tolerance: h["x1"] = max_x

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
                    if v["top"] <= ymin + tolerance and v["bottom"] >= ymax - tolerance: return True
        elif abs(y1 - y2) < tolerance:
            xmin, xmax = min(x1, x2), max(x1, x2)
            for h in self.lines["horizontal"]:
                if abs(h["y"] - y1) < tolerance:
                    if h["x0"] <= xmin + tolerance and h["x1"] >= xmax - tolerance: return True
        return False

    def _generate_minimal_cells(self):
        raw_cells = []
        for x1, y1 in self.intersections:
            rights = [p for p in self.intersections if abs(p[1] - y1) < 1.2 and p[0] > x1]
            belows = [p for p in self.intersections if abs(p[0] - x1) < 1.2 and p[1] > y1]
            if not rights or not belows: continue

            cell_found = False
            for p_right in sorted(rights, key=lambda p: p[0]):
                x2 = p_right[0]
                if not self._is_connected(x1, y1, x2, y1): continue
                for p_below in sorted(belows, key=lambda p: p[1]):
                    y2 = p_below[1]
                    if not self._is_connected(x1, y1, x1, y2): continue
                    if not self._is_connected(x1, y2, x2, y2): continue
                    if not self._is_connected(x2, y1, x2, y2): continue

                    has_internal_nodes = any(x1 < p[0] < x2 and y1 < p[1] < y2 for p in self.intersections)
                    if not has_internal_nodes:
                        bbox = (round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1))
                        if bbox not in raw_cells: 
                            raw_cells.append(bbox)
                        cell_found = True
                        break
                if cell_found: break

        # 🚫 ВБИВАЄМО МІКРО-ПРЯМОКУТНИКИ (від подвійних ліній)
        self.cells = []
        for bbox in raw_cells:
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            # Якщо висота комірки менша за 8 пікселів або ширина менша за 15 — це графічне сміття
            if w >= 15.0 and h >= 8.0:
                self.cells.append(bbox)

        if not self.cells: return

        x_set = set([round(bbox[0], -1) for bbox in self.cells])
        self.columns_x = sorted(list(x_set))
        y_set = set([round(bbox[1], -1) for bbox in self.cells])
        self.rows_y = sorted(list(y_set))

    def _extract_custom_text(self, page, bbox):
        cropped = page.crop(bbox, strict=False)
        raw_chars = [c for c in cropped.chars if c['width'] > 0 and c['height'] > 2]
        
        chars = []
        for c in raw_chars:
            color = c.get('non_stroking_color')
            if color in [(1, 1, 1), [1, 1, 1], (1,), 1, '1', 1.0, (1.0, 1.0, 1.0)]:
                continue
                
            is_colliding = False
            for i, prev_c in enumerate(chars):
                x_overlap = max(0, min(c['x1'], prev_c['x1']) - max(c['x0'], prev_c['x0']))
                y_overlap = max(0, min(c['bottom'], prev_c['bottom']) - max(c['top'], prev_c['top']))
                
                char_area = (c['x1'] - c['x0']) * (c['bottom'] - c['top'])
                if char_area > 0:
                    overlap_ratio = (x_overlap * y_overlap) / char_area
                    if overlap_ratio > 0.4:
                        chars[i] = c 
                        is_colliding = True
                        break
                        
            if not is_colliding:
                chars.append(c)

        if not chars: return ""

        x0, y0, x1, y1 = bbox
        mid_y = (y0 + y1) / 2

        is_first_column = False
        if self.columns_x and abs(x0 - self.columns_x[0]) < 15: is_first_column = True
        if is_first_column:
            chars.sort(key=lambda c: c['bottom'], reverse=True)
            return "".join([c['text'] for c in chars]).strip()

        chars.sort(key=lambda c: c['top'])

        lines = []
        for c in chars:
            placed = False
            for line in lines:
                line_top = sum(char['top'] for char in line) / len(line)
                line_bottom = sum(char['bottom'] for char in line) / len(line)
                
                overlap = max(0, min(c['bottom'], line_bottom) - max(c['top'], line_top))
                char_h = c['bottom'] - c['top']
                line_h = line_bottom - line_top
                
                if char_h > 0 and (overlap / min(char_h, line_h)) > 0.4:
                    line.append(c)
                    placed = True
                    break
            
            if not placed:
                lines.append([c])
                
        lines.sort(key=lambda l: sum(c['top'] for c in l) / len(l))
        for line in lines:
            line.sort(key=lambda c: c['x0'])

        top_lines, bottom_lines = [], []
        for line in lines:
            line_mid_y = sum((c['top'] + c['bottom'])/2 for c in line) / len(line)
            if line_mid_y < mid_y: top_lines.append(line)
            else: bottom_lines.append(line)

        def process_block(block_lines):
            if not block_lines: return None
            right_threshold = x0 + (x1 - x0) * 0.60
            right_starts = []

            for line in block_lines:
                if not line: continue
                current_word_start = line[0]['x0']
                for i in range(1, len(line)):
                    if line[i]['x0'] - line[i-1]['x1'] > self.text_gap_threshold:
                        if current_word_start > right_threshold: right_starts.append(current_word_start)
                        current_word_start = line[i]['x0']
                if current_word_start > right_threshold: right_starts.append(current_word_start)

            split_x = min(right_starts) - 2 if right_starts else x1 + 999
            left_lines_text, right_lines_text = [], []

            for line in block_lines:
                words, current_word = [], [line[0]]
                for c in line[1:]:
                    if c['x0'] - current_word[-1]['x1'] > self.text_gap_threshold:
                        words.append(current_word)
                        current_word = [c]
                    else: current_word.append(c)
                words.append(current_word)

                l_parts, r_parts = [], []
                for word in words:
                    word_text = "".join(c['text'] for c in word).strip()
                    if word[0]['x0'] < split_x: l_parts.append(word_text)
                    else: r_parts.append(word_text)

                if l_parts: left_lines_text.append(" ".join(l_parts))
                if r_parts: right_lines_text.append(" ".join(r_parts))

            left_text = ""
            for chunk in left_lines_text:
                if left_text:
                    if left_text.endswith("-"): 
                        left_text = left_text[:-1] + chunk 
                    else: 
                        left_text += " " + chunk
                else: 
                    left_text = chunk

            right_text = " ".join(right_lines_text).strip()
            return f"{left_text} | {right_text}" if right_text else left_text

        res = []
        top_text = process_block(top_lines)
        if top_text: res.append(top_text)
        bottom_text = process_block(bottom_lines)
        if bottom_text: res.append(bottom_text)
        return "\n".join(res)

    def parse_table_geometric(self, page_index: int = 0) -> dict:
        self.rect_text_dict = {}
        with pdfplumber.open(self.pdf_path) as pdf:
            page = pdf.pages[page_index]
            
            self._extract_and_normalize_lines(page)
            self._heal_table_grid()
            self._find_intersections()
            self._generate_minimal_cells()

            for coords in self.cells:
                text = self._extract_custom_text(page, coords)
                self.rect_text_dict[coords] = text if text else ""
        return self.rect_text_dict

    def export_schedule_to_json(self, output_path: str = None) -> dict:
        if not self.rect_text_dict:
            return {}

        cols_x = sorted(self.columns_x)
        if len(cols_x) < 3: return {}

        col_day_x = cols_x[0]
        col_time_x = cols_x[1]
        group_cols_x = cols_x[2:]

        group_col_centers = {}
        for i, gc in enumerate(group_cols_x):
            if i < len(group_cols_x) - 1:
                group_col_centers[gc] = (gc + group_cols_x[i+1]) / 2
            else:
                width = group_cols_x[-1] - group_cols_x[-2] if len(group_cols_x) > 1 else 100
                group_col_centers[gc] = gc + width / 2

        groups_headers = {}
        header_candidates = {gc: [] for gc in group_cols_x}

        for bbox, text in self.rect_text_dict.items():
            if not text.strip(): continue
            x0, y0, x1, y1 = bbox
            for gc in group_cols_x:
                if abs(x0 - gc) < 10:
                    header_candidates[gc].append({"bbox": bbox, "text": text.replace('\n', ' ').strip()})
                    break

        for gc, cands in header_candidates.items():
            if cands:
                cands.sort(key=lambda c: c["bbox"][1])
                groups_headers[gc] = cands[0]["text"]
            else:
                groups_headers[gc] = f"Group_Col_{int(gc)}"

        schedule_start_y = max([cands[0]["bbox"][3] for cands in header_candidates.values() if cands]) + 2 if header_candidates else 0

        days_cells, times_cells, data_cells = [], [], []

        for bbox, text in self.rect_text_dict.items():
            x0, y0, x1, y1 = bbox
            if y0 < schedule_start_y - 5: continue

            if abs(x0 - col_day_x) < 10:
                if text.strip(): days_cells.append({"bbox": bbox, "text": text.replace('\n', ' ').strip()})
            elif abs(x0 - col_time_x) < 10:
                if text.strip(): times_cells.append({"bbox": bbox, "text": text.replace('\n', ' ').strip()})
            elif x0 >= group_cols_x[0] - 10:
                data_cells.append({"bbox": bbox, "text": text.strip()})

        garbage_words = ["пара", "час", "години", "день", "дні", "група", "підгрупа"]

        valid_times_cells = []
        for t in times_cells:
            cln_txt = t["text"].lower().strip()
            if any(gw in cln_txt for gw in garbage_words): continue
            if len(cln_txt) <= 1 and cln_txt.isdigit(): continue
            valid_times_cells.append(t)
        times_cells = valid_times_cells

        valid_days_cells = []
        for d in days_cells:
            cln_txt = d["text"].lower().strip()
            if any(gw in cln_txt for gw in garbage_words): continue
            valid_days_cells.append(d)
        days_cells = sorted(valid_days_cells, key=lambda c: c["bbox"][1])

        schedule_json = {groups_headers[gc]: {} for gc in group_cols_x}

        for cell in data_cells:
            cb = cell["bbox"]
            x0, y0, x1, y1 = cb
            cy_center = (y0 + y1) / 2
            c_h = y1 - y0

            matched_groups = []
            for gc, center_x in group_col_centers.items():
                if x0 - 5 <= center_x <= x1 + 5:
                    matched_groups.append(gc)

            if not matched_groups:
                for gc in reversed(group_cols_x):
                    if (x0 + x1)/2 > gc - 5:
                        matched_groups.append(gc)
                        break
            if not matched_groups: continue

            matched_day = "Невідомий день"
            for i, d in enumerate(days_cells):
                d_y0 = d["bbox"][1]
                d_y1 = days_cells[i+1]["bbox"][1] if i + 1 < len(days_cells) else 9999
                if d_y0 - 5 <= cy_center < d_y1 + 5:
                    matched_day = d["text"]
                    break

            matched_time, time_bbox = "Невідома пара", None
            for t in times_cells:
                tb = t["bbox"]
                if tb[1] - 5 <= cy_center <= tb[3] + 5:
                    matched_time, time_bbox = t["text"], tb
                    break

            if not time_bbox: continue

            _, t_y0, _, t_y1 = time_bbox
            t_h = t_y1 - t_y0
            t_mid_y = (t_y0 + t_y1) / 2

            if c_h >= (t_h * 0.8):
                position = "mono"
            else:
                if cy_center < t_mid_y:
                    position = "top"
                else:
                    position = "bottom"

            for gc in matched_groups:
                grp_name = groups_headers[gc]
                if matched_day not in schedule_json[grp_name]:
                    schedule_json[grp_name][matched_day] = {}
                if matched_time not in schedule_json[grp_name][matched_day]:
                    schedule_json[grp_name][matched_day][matched_time] = []

                schedule_json[grp_name][matched_day][matched_time].append({
                    "position": position,
                    "text": cell["text"]
                })

        for grp_name in schedule_json:
            for day in schedule_json[grp_name]:
                for time, classes in schedule_json[grp_name][day].items():
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
                            
                    schedule_json[grp_name][day][time] = unique_classes

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(schedule_json, f, ensure_ascii=False, indent=4)
            print(f"✅ Структурований розклад успішно збережено у файл: {output_path}")

        return schedule_json
    
    def visualize_debug(self, page_index: int = 0, output_image_path: str = "debug_vision.png"):
        if not self.cells and not self.intersections:
            print("⚠️ Немає даних для візуалізації. Спочатку запустіть parse_table_geometric()!")
            return

        with pdfplumber.open(self.pdf_path) as pdf:
            page = pdf.pages[page_index]
            im = page.to_image(resolution=150)
            
            if self.cells:
                im.draw_rects(self.cells, stroke="red", stroke_width=2, fill=None)
            if self.intersections:
                im.draw_circles(self.intersections, radius=3, stroke="green", fill="green")
                
            im.save(output_image_path)
            print(f"👁️ Рентген-візуалізацію парсера збережено у файл: {output_image_path}")

# --- ТЕСТОВИЙ БЛОК ---
if __name__ == "__main__":
    
    parser = PDFGeometricTableParser(r"1_kurs_v0.3.pdf", text_gap_threshold=5.0) 

    print("⏳ Розпізнавання геометричної таблиці...")
    parsed_cells = parser.parse_table_geometric(page_index=0) 

    print("📸 Створення візуалізації...")
    parser.visualize_debug(page_index=0, output_image_path="debug_vision.png")

    print("\n--- Експорт Розкладу в JSON ---")
    schedule_data = parser.export_schedule_to_json("output_schedule.json")