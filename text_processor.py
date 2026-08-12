import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple


@dataclass
class LessonData:
    group: str
    day: str
    lesson_number: int
    time_str: str
    week_type: str
    subject: str
    lesson_type: Optional[str] = None
    teacher_title: Optional[str] = None
    teacher_name: Optional[str] = None
    room: Optional[str] = None


class ScheduleTextProcessor:
    def _count_pipes(self, text: str) -> int:
        """Рахує кількість вертикальних рисок '|' у сирому тексті"""
        if not text:
            return 0
        return text.count('|')
    
    def _clear_pipes(self, text):
        """"Заміняє вертикальні рисочки на пустоту"""
        return text.replace('|','')

    def _clean_whitespace(self, text):
        """Багато пустоти перетворює на одну пустоту"""
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()
    
    def _extract_lesson_number(self, time_key: str) -> int:
        """Витягує номер пари з ключа часу (наприклад, 'IV 13:30-14:50' -> 4)"""
        roman_map = {
            'VIII': 8, 'VII': 7, 'VI': 6, 'IV': 4, 'V': 5, 
            'III': 3, 'II': 2, 'I': 1
        }
        clean_key = time_key.strip()
        for roman, num in roman_map.items():
            if re.match(rf'^{roman}\b', clean_key):
                return num
        
        match = re.search(r'^\d+', clean_key)
        if match:
            return int(match.group(0))
        return 1

    def _extract_time(self, time_key: str) -> str:
        """Витягує часовий проміжок (наприклад, '13:30-14:50')"""
        match = re.search(r'\d{1,2}:\d{2}-\d{1,2}:\d{2}', time_key)
        return match.group(0) if match else ""

    def _extract_room(self, text: str) -> Tuple[Optional[str], str]:
        """Шукає аудиторію та вирізає її з тексту"""
        match = re.search(r'(?:ауд\.\s*|\|\s*)(\d+[а-яА-Яa-zA-Z]?)', text)
        if match:
            room = match.group(1)
            clean_text = text.replace(match.group(0), "").strip()
            return room, clean_text
        return None, text

    def _extract_lesson_type(self, text: str) -> Tuple[Optional[str], str]:
        """Шукає тип пари"""
        pattern = r'\(?\b(лек|пр|практ|лаб)\b\.?\)?'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw_type = match.group(0).strip("()")  # залишаємо скорочення (наприклад, "лек." або "пр.")
            clean_text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            return raw_type, clean_text
        return None, text

    def _extract_teacher_title(self, text: str) -> Tuple[Optional[str], str]:
        """Шукає звання викладача"""
        titles = [r'\bдоц\b\.?', r'\bпроф\b\.?', r'\bст\.вик\b\.?']
        found_titles = []
        clean_text = text
        for pattern in titles:
            matches = re.findall(pattern, clean_text, re.IGNORECASE)
            if matches:
                found_titles.extend([m.lower().replace(".", "") for m in matches])
                clean_text = re.sub(pattern, "", clean_text, flags=re.IGNORECASE).strip()
        
        if found_titles:
            return ", ".join(dict.fromkeys(found_titles)), clean_text
        return None, text

    def _extract_teacher_name(self, text: str) -> Tuple[Optional[str], str]:
        """Шукає ПІБ викладачів"""
        pattern = r'([А-ЯҐЄІЇ][а-щзьюяґєії\'`]+)\s+([А-ЯҐЄІЇ]\.\s?[А-ЯҐЄІЇ]\.)'
        matches = re.findall(pattern, text)
        if matches:
            names = [f"{m[0]} {m[1]}" for m in matches]
            clean_text = re.sub(pattern, "", text).strip()
            return ", ".join(names), clean_text
        return None, text

    def _extract_subject(self, text: str) -> str:
        """Очищає залишок тексту від зайвих розділових знаків"""
        cleaned = re.sub(r'^[,\.\|\s\-\(\)]+|[,\.\|\s\-\(\)]+$', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    def parse_specific_lesson(self,raw_text, position, time_key, day, group):
        """"Метод обробки пари специфічний"""
        lesson_num = self._extract_lesson_number(time_key)
        time_str = self._extract_time(time_key)
        week_type_map = {
            "mono": "Статичний",
            "top": "Чисельник",
            "bottom": "Знаменник"
        }
        week_type = week_type_map.get(position, position)
        if not raw_text or not raw_text.strip():
            lesson = LessonData(
                group=group,
                day=day,
                lesson_number=lesson_num,
                time_str=time_str,
                week_type=week_type,
                subject=""
            )
            return asdict(lesson)
        raw_text = self._clear_pipes(raw_text)
        raw_text = self._clean_whitespace(raw_text)
        lesson = LessonData(
            group=group,
            day=day,
            lesson_number=lesson_num,
            time_str=time_str,
            week_type=week_type,
            subject=raw_text
        )
        return asdict(lesson)

    def parse_lesson(self, raw_text: str, position: str, time_key: str, day : str, group : str) -> Dict[str, Any]:
        """Головний метод розбору однієї пари"""
        lesson_num = self._extract_lesson_number(time_key)
        time_str = self._extract_time(time_key)

        week_type_map = {
            "mono": "Статичний",
            "top": "Чисельник",
            "bottom": "Знаменник"
        }
        week_type = week_type_map.get(position, position)

        if not raw_text or not raw_text.strip():
            lesson = LessonData(

                lesson_number=lesson_num,
                time_str=time_str,
                week_type=week_type,
                subject="",
                day=day,
                group=group
            )
            return asdict(lesson)

        working_text = raw_text
        room, working_text = self._extract_room(working_text)
        lesson_type, working_text = self._extract_lesson_type(working_text)
        teacher_title, working_text = self._extract_teacher_title(working_text)
        teacher_name, working_text = self._extract_teacher_name(working_text)
        subject = self._extract_subject(working_text)

        lesson = LessonData(
            lesson_number=lesson_num,
            time_str=time_str,
            week_type=week_type,
            subject=subject,
            lesson_type=lesson_type,
            teacher_title=teacher_title,
            teacher_name=teacher_name,
            room=room,
            day=day,
            group=group
        )
        return asdict(lesson)

    def process_schedule(self, full_schedule: Dict[str, Any]) -> Dict[str, Any]:
        """Обходить весь словник розкладу та обробляє всі пари"""
        processed_schedule = {}
        for group, days in full_schedule.items():
            processed_schedule[group] = {}
            for day, times in days.items():
                processed_schedule[group][day] = []
                for time_key, lessons in times.items():    
                    for item in lessons:
                        raw_text = item.get("text", "")
                        position = item.get("position", "mono")
                        pipe_count = self._count_pipes(raw_text)
                        if pipe_count < 2:
                            parsed = self.parse_specific_lesson(
                                day=day,
                                group=group,
                                raw_text=raw_text,
                                position=position,
                                time_key=time_key
                            )
                        else:
                            parsed = self.parse_lesson(
                                raw_text=item.get("text", ""),
                                position=item.get("position", "mono"),
                                time_key=time_key,
                                day=day,
                                group=group
                            )
                        processed_schedule[group][day].append(parsed)
        return processed_schedule