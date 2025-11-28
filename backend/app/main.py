from fastapi import FastAPI

app = FastAPI(title="AI Distributed Systems Assistant")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "AI Distributed Systems Assistant backend is running"}
