"""Actual adapter progress numeric payloads are retained without leaking arbitrary backend objects."""
from pathlib import Path
from mbnas_contracts.schemas.jobs import ProgressMessage
from mbnas_core.owner_worker.main import Worker, _Live, _numeric_training_progress


def test_numeric_progress_keeps_real_native_loss_speed_only():
    assert _numeric_training_progress({"train_loss":1.2,"loss":0.7,"images_per_second":20.5,"throughput":13.0,
        "native_metrics":{"train/box_loss":0.2,"metrics/mAP50(B)":0.7,"bad":float("nan"),"flag":True,
                          "traceback":"source secret","nested":{"loss":1}},"weights":"/private/model.py"}) == {
        "train_loss":1.2,"loss":0.7,"images_per_second":20.5,"throughput":13.0,
        "native_metrics":{"train/box_loss":0.2,"metrics/mAP50(B)":0.7}}


def test_handle_line_progress_preserves_payload_for_common_monitor():
    worker=Worker.__new__(Worker);live=_Live();job={"id":"j"}
    msg=ProgressMessage(type="progress",job_id="j",ts="2026-10-21T00:00:00Z",epoch=2,epoch_target_total=3,
        global_step=11,learning_rate=0.01,payload={"native_metrics":{"train/box_loss":0.25},"train_loss":0.5,
                                                "images_per_second":9.0,"private":{"source":"secret"}})
    assert worker._handle_line(job,msg.to_line(),live,Path("/tmp")) is None
    assert live.progress["native_metrics"]=={"train/box_loss":0.25}
    assert live.progress["train_loss"]==0.5 and live.progress["images_per_second"]==9.0
    assert live.progress["epoch"]==2 and live.progress["learning_rate"]==0.01
    assert "private" not in live.progress
