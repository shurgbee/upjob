FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 texlive-latex-base texlive-latex-extra texlive-fonts-recommended && rm -rf /var/lib/apt/lists/*
COPY compile_runner.py /opt/compile_runner.py
ENV openin_any=p openout_any=p TEXMFOUTPUT=/work
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python3", "/opt/compile_runner.py"]
