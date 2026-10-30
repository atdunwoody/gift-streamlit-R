FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    r-base-core r-cran-dplyr r-cran-zoo r-cran-jsonlite \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN Rscript --vanilla setup_r.R && python check_r.py
EXPOSE 8501
CMD ["sh", "-c", "exec python -m streamlit run app.py --server.address=0.0.0.0 --server.port=\"${PORT:-8501}\" --server.headless=true"]
