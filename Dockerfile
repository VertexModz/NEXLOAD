FROM python:3.11-slim

# ติดตั้ง ffmpeg (จำเป็นสำหรับ merge video+audio)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nexload_web.py .

# สร้างโฟลเดอร์บันทึกวิดีโอ
RUN mkdir -p nexload_video

EXPOSE 8888

CMD ["python", "nexload_web.py"]
