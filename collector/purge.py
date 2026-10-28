"""Retain only the most recent inventory runs and their observations."""

import os
from collections.abc import Sequence

from sqlalchemy import delete, select

from app import db
from app.models import Anomaly, ConsolidatedAsset, Run


def purge_old_runs(keep: int | None = None) -> int:
    """Delete older runs in a new transaction; return the number removed.

    When keep is omitted, RUN_RETENTION defaults to 30. Values must be
    positive integers. Requires an application context and an idle session.
    """
    if keep is None:
        configured = os.environ.get("RUN_RETENTION", "30")
        try:
            keep = int(configured)
        except ValueError as error:
            raise ValueError("RUN_RETENTION must be a positive integer") from error
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("run retention must be a positive integer")

    with db.session.begin():
        old_ids: Sequence[int] = db.session.scalars(
            select(Run.id).order_by(Run.started_at.desc(), Run.id.desc()).offset(keep)
        ).all()
        if old_ids:
            # RESTRICT references need removing before database cascades remove the run's assets.
            db.session.execute(delete(Anomaly).where(Anomaly.run_id.in_(old_ids)))
            db.session.execute(delete(ConsolidatedAsset).where(ConsolidatedAsset.run_id.in_(old_ids)))
            db.session.execute(delete(Run).where(Run.id.in_(old_ids)))
    return len(old_ids)
