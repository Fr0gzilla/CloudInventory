"""Admit an inventory run before releasing the HTTP request."""

import threading

from flask import current_app

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.inventory_runner import _now, _run_inventory
from collector.run_lock import RunAlreadyActive, run_lock


def recover_interrupted_runs():
    lock = run_lock()
    try:
        lock.__enter__()
    except RunAlreadyActive:
        return
    try:
        interrupted = [row.id for row in db.session.query(Run.id).filter_by(status="RUNNING")]
        if interrupted:
            for model in (Anomaly, ConsolidatedAsset, IpamRecord, Asset):
                db.session.query(model).filter(model.run_id.in_(interrupted)).delete(
                    synchronize_session=False,
                )
        db.session.query(Run).filter_by(status="RUNNING").update({
            "status": "FAIL", "finished_at": _now(),
            "error_message": "Inventory run interrupted",
        })
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        lock.__exit__(None, None, None)


def start_inventory():
    app = current_app._get_current_object()
    lock = run_lock()
    lock.__enter__()
    try:
        run = Run(status="RUNNING", started_at=_now(), run_trigger="manuel",
                  current_step=1, completed_steps=1)
        db.session.add(run)
        db.session.commit()
        identifier = run.id

        def worker():
            try:
                with app.app_context():
                    _run_inventory(run_id=identifier)
            except Exception:
                app.logger.exception("Inventory run %s failed unexpectedly", identifier)
            finally:
                lock.__exit__(None, None, None)

        thread = threading.Thread(target=worker, name=f"inventory-{identifier}", daemon=True)
        thread.start()
        return identifier
    except Exception:
        db.session.rollback()
        try:
            if "identifier" in locals():
                db.session.query(Run).filter_by(id=identifier).update({
                    "status": "FAIL", "finished_at": _now(),
                    "error_message": "Inventory worker could not start",
                })
                db.session.commit()
        finally:
            lock.__exit__(None, None, None)
        raise
