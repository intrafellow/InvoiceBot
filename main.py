import logging_conf

from routers.user_router import router as user_router
from routers.template_router import router as template_router
from routers.file_router import router as file_router
from routers.health_router import router as health_router
from fastapi import FastAPI


app = FastAPI(
    title="InvoiceBot API",
    version="1.0.0"
)

app.include_router(user_router)
app.include_router(template_router)
app.include_router(file_router)
app.include_router(health_router)
