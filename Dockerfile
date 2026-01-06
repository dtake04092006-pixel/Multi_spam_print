# Sử dụng Python 3.11.9
FROM python:3.11.9-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# --- [QUAN TRỌNG] THÊM 'git' VÀO DANH SÁCH CÀI ĐẶT ---
RUN apt-get update && apt-get install -y \
    git \
    tesseract-ocr \
    libtesseract-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements và cài đặt thư viện
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Mở port
EXPOSE 10000

# Chạy bot
CMD ["python", "main.py"]
