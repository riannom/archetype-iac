from __future__ import annotations

from uuid import uuid4

from app import models
from app.utils.link import links_needing_reconciliation_filter


def test_links_needing_reconciliation_includes_same_host_error_links(test_db, sample_lab, sample_host):
    include = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        is_cross_host=False,
        desired_state="up",
        actual_state="error",
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
    )
    exclude = models.LinkState(
        id=str(uuid4()),
        lab_id=sample_lab.id,
        link_name="r3:eth1-r4:eth1",
        source_node="r3",
        source_interface="eth1",
        target_node="r4",
        target_interface="eth1",
        is_cross_host=False,
        desired_state="down",
        actual_state="error",
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
    )
    test_db.add_all([include, exclude])
    test_db.commit()

    matches = (
        test_db.query(models.LinkState)
        .filter(models.LinkState.lab_id == sample_lab.id)
        .filter(links_needing_reconciliation_filter())
        .all()
    )
    matched_ids = {row.id for row in matches}
    assert include.id in matched_ids
    assert exclude.id not in matched_ids
