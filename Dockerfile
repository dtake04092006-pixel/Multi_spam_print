# Sử dụng Python 3.10 trên nền Linux nhẹ (Slim)
FROM python:3.10-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt các gói hệ thống cần thiết (QUAN TRỌNG: Tesseract OCR)
# libgl1 và libglib2.0-0 cần cho OpenCV
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements và cài đặt thư viện Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code vào
COPY . .

# Mở port cho Web UI
EXPOSE 10000

# Lệnh chạy bot
CMD ["python", "main.py"]
