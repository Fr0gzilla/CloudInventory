"""Persistent schedules backed by the application database."""

from datetime import UTC

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from app import db
from app.audit import scheduled_run
from app.models import Run, RunSchedule
from collector.async_runner import start_inventory
from collector.run_lock import RunAlreadyActive

scheduling = Blueprint("scheduling", __name__)


def _trigger(frequency):
    return CronTrigger(minute=0, hour=0 if frequency == "quotidien" else "*", timezone=UTC)


def _next_at(job):
    return job.next_run_time.isoformat() if job and job.next_run_time else None


def fire_schedule(app, schedule_id):
    """Run one due schedule through the existing asynchronous admission path."""
    with app.app_context():
        schedule = db.session.get(RunSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        try:
            token = scheduled_run.set(schedule_id)
            try:
                identifier = start_inventory()
            finally:
                scheduled_run.reset(token)
        except RunAlreadyActive:
            app.logger.info("Scheduled run %s skipped: inventory already active", schedule_id)
            return
        except Exception:
            app.logger.exception("Scheduled run %s could not start", schedule_id)
            return
        try:
            db.session.query(Run).filter_by(id=identifier).update({
                "run_trigger": "planifié", "schedule_id": schedule_id,
            })
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not record provenance for scheduled run %s", identifier)


def sync_schedule(app, schedule):
    """Replace the job associated with an updated schedule and persist its next UTC due date."""
    scheduler = app.extensions.get("inventory_scheduler")
    if scheduler is None:
        schedule.next_at = None
        return
    job_id = f"schedule-{schedule.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if schedule.enabled:
        job = scheduler.add_job(
            fire_schedule, _trigger(schedule.frequency), args=(app, schedule.id), id=job_id,
            max_instances=1, coalesce=True, misfire_grace_time=30,
        )
        schedule.next_at = _next_at(job)
    else:
        schedule.next_at = None


def start_scheduler(app):
    """Install one scheduler per application, loading enabled schedules from SQLite."""
    scheduler = BackgroundScheduler(timezone=UTC, daemon=True)
    app.extensions["inventory_scheduler"] = scheduler
    scheduler.start(paused=True)
    try:
        with app.app_context():
            for schedule in db.session.query(RunSchedule).all():
                sync_schedule(app, schedule)
            db.session.commit()
    except Exception:
        db.session.rollback()
        scheduler.shutdown(wait=False)
        app.extensions.pop("inventory_scheduler", None)
        raise
    scheduler.resume()


@scheduling.get("/scheduling")
@login_required
def schedules_page():
    schedules = db.session.query(RunSchedule).order_by(RunSchedule.id).all()
    return render_template("scheduling.html", schedules=schedules)


@scheduling.post("/scheduling")
@login_required
def save_schedule():
    frequency = request.form.get("frequency")
    enabled = request.form.get("enabled")
    identifier = request.form.get("schedule_id", "")
    if frequency not in ("horaire", "quotidien") or enabled not in ("0", "1"):
        abort(400)
    if identifier and (not identifier.isascii() or not identifier.isdecimal() or
                       len(identifier) > 18 or int(identifier) < 1):
        abort(400)
    schedule = db.session.get(RunSchedule, int(identifier)) if identifier else RunSchedule()
    if schedule is None:
        abort(404)
    schedule.frequency = frequency
    schedule.enabled = int(enabled)
    try:
        db.session.add(schedule)
        db.session.commit()
        sync_schedule(current_app._get_current_object(), schedule)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not save run schedule")
        abort(500)
    return redirect(url_for("scheduling.schedules_page"))
