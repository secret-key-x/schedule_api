import json
import os
from geometric_parser import PDFGeometricTableParser

def inspect_course(pdf_name):
    print(f"Inspecting {pdf_name}...")
    if not os.path.exists(pdf_name):
        print(f"File {pdf_name} not found.")
        return
    
    parser = PDFGeometricTableParser(pdf_name)
    parser.parse_table_geometric()
    
    # Debug raw cells for the first page
    print(f"  Raw cells from first page (first 10):")
    p0_cells = [c for c in parser.parsed_pages_cells if c["page"] == 0]
    for i in range(min(10, len(p0_cells))):
        text = p0_cells[i]['text']
        encoded = text.encode('utf-8', errors='replace')
        print(f"    {i}: col={p0_cells[i]['col_idx']} text='{text}' hex={text.encode('utf-16').hex()}")

    schedule = parser.export_schedule_to_json()
    
    output_name = f"inspect_{pdf_name}.json"
    with open(output_name, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)
    
    groups = list(schedule.keys())
    print(f"Found groups: {groups}")
    
    if not groups:
        print(f"  No data parsed for {pdf_name}.")
        # Спробуємо глянути на сирі клітинки
        print(f"  Parsed {len(parser.parsed_pages_cells)} raw cells")
        if parser.parsed_pages_cells:
             print(f"  Sample cell text (first 5):")
             for i in range(min(5, len(parser.parsed_pages_cells))):
                 print(f"    {i}: '{parser.parsed_pages_cells[i]['text']}'")
             # Подивимось які взагалі є col_idx
             cols = set(c['col_idx'] for c in parser.parsed_pages_cells)
             print(f"  Columns found: {cols}")
    else:
        print(f"  Sample group data for '{groups[0]}':")
        day = list(schedule[groups[0]].keys())[0] if schedule[groups[0]] else None
        if day:
            print(f"    Day: {day}, Pairs: {list(schedule[groups[0]][day].keys())}")
    
    # For each group, check if there's a 1st pair
    for group in groups:
        first_pairs = []
        for day, pairs in schedule[group].items():
            for time in pairs:
                if "08:30" in time or "8:30" in time:
                    first_pairs.append(f"{day}: {time}")
        if not first_pairs:
            print(f"  WARNING: Group {group} might be missing 1st pair (08:30)")

if __name__ == "__main__":
    for i in [4, 5, 6]:
        inspect_course(f"{i}_kurs_v0.5.pdf")
