FROM python:3.10

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app

# ustawienie punktu startowego
CMD ["python", "app/espi_monitor.py"]