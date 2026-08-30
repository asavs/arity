from __future__ import annotations

import threading
from pathlib import Path

from gorkbot.handlers import JsonlRecordStore
from gorkbot.types import StoreRecord


def test_parallel_appends_remain_complete_json_lines(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "records")
    workers = 16
    records_per_worker = 40

    def append_batch(worker: int) -> None:
        for sequence in range(records_per_worker):
            store.append(
                StoreRecord(
                    kind="events",
                    record={"worker": worker, "sequence": sequence},
                )
            )

    threads = [threading.Thread(target=append_batch, args=(worker,)) for worker in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = store.query("events")
    identities = {(record["worker"], record["sequence"]) for record in records}
    assert len(records) == workers * records_per_worker
    assert len(identities) == workers * records_per_worker
