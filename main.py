from fastapi import FastAPI, UploadFile, File, Form, HTTPException
import httpx
import tempfile
import os

from geometric_parser import PDFGeometricTableParser
from text_processor import ScheduleTextProcessor

app = FastAPI()

@app.get("/")
def health_check():
    return {"message": "Сервер живий!"}

@app.post("/parse")
async def parse_schedule(file: UploadFile = File(None), url: str = Form(None)):
    if not file and not url:
        raise HTTPException(status_code=400, detail="Необхідно надати файл або URL на PDF")
    
    fd, temp_file_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    
    try:
        if file:
            with open(temp_file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
                
        elif url:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                with open(temp_file_path, "wb") as buffer:
                    buffer.write(response.content)

        parser = PDFGeometricTableParser(rf"{temp_file_path}", text_gap_threshold=5.0)

        parser.parse_table_geometric()
        schedule_data = parser.export_schedule_to_json()

        schedule_processor = ScheduleTextProcessor()
        processed_schedule = schedule_processor.process_schedule(schedule_data)

        return processed_schedule

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Помилка обробки: {str(e)}")
        
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)