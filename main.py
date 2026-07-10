import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from api_end_point.agent_router import router as ai_agent_router
from auth.api_router import router as auth_router
from notification.dashboard import router as dashboard_router
from notification.notifier_scheduled import run_full_workflow
from dotenv import load_dotenv


load_dotenv()


scheduler = BackgroundScheduler()

RUN_SCHEDULER = os.getenv("RUN_SCHEDULER", "false").lower() == "true"



@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n[APP] FastAPI server starting...")

    if RUN_SCHEDULER:
        scheduler.add_job(
            func=run_full_workflow,
            trigger=CronTrigger(
                hour=7,
                minute=55,
                timezone='Europe/Amsterdam',
            ),
            id="daily_pipeline",
            name="Daily Scraping & Matching Pipeline",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )

        scheduler.start()
        next_run = scheduler.get_job("daily_pipeline").next_run_time
        print(f"[APP] Daily pipeline scheduled at: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"[APP] Scheduler running: {scheduler.running}")
    else:
        print("[APP] Scheduler disabled on this instance (RUN_SCHEDULER=false).")

    print("[APP] Server is ready.\n")
    yield

    print("\n[APP] FastAPI server shutting down...")
    if scheduler.running:
        scheduler.shutdown()
    print("[APP] Goodbye.\n")


app = FastAPI(
    title="AI Agent API",
    description="API for AI Agent and Web Scrapping",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ai_agent_router, prefix="/api", tags=["Agent"])
app.include_router(auth_router, prefix="/api", tags=["Authentication"])
app.include_router(dashboard_router, prefix="/api", tags=["Dashboard"])
