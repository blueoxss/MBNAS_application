"""S5-F4 (CPU): the RF-DETR train loader draws its sampler order from the MBNAS generator, so a saved
``loader_generator_state`` continues the order in a fresh trainer (no GPU, no training)."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
module_data = pytest.importorskip("rfdetr.training.module_data")

from mbnas_model_runtime.adapters import rfdetr_adapter as ra  # noqa: E402
from mbnas_model_runtime.adapters.base import CapabilityError  # noqa: E402

DATA = list(range(10))


def _order(loader) -> list[int]:
    return [int(v) for batch in loader for v in batch]


@pytest.fixture
def fake_train_dataloader(monkeypatch):
    """Stand-in for RF-DETR's map-style train loader: shuffle=True, no generator (what rfdetr 1.11.2 builds)."""
    kind = {"sampler": "random"}

    def train_dataloader(self):
        if kind["sampler"] == "random":
            return torch.utils.data.DataLoader(DATA, batch_size=2, shuffle=True)
        return torch.utils.data.DataLoader(DATA, batch_size=2, sampler=torch.utils.data.SequentialSampler(DATA))

    monkeypatch.setattr(module_data.RFDETRDataModule, "train_dataloader", train_dataloader)
    return kind


def test_generator_state_continues_the_sampler_order(fake_train_dataloader) -> None:
    gen = torch.Generator().manual_seed(101)
    record: dict = {}
    with ra._seeded_train_loader(gen, record):
        loader = module_data.RFDETRDataModule.train_dataloader(object())
    assert record == {"sampler": "RandomSampler", "sampler_generator_attached": True, "loader_generator_attached": True}
    assert loader.generator is gen and loader.sampler.generator is gen
    epoch0 = _order(loader)
    saved = gen.get_state().clone()  # what the epoch-end full state stores
    torch.manual_seed(999)  # a fresh process / PTL seed_everything re-seeds the global RNG: must not matter
    epoch1 = _order(loader)
    resumed = torch.Generator()
    resumed.set_state(saved)
    with ra._seeded_train_loader(resumed, {}):
        fresh = module_data.RFDETRDataModule.train_dataloader(object())
    torch.manual_seed(12345)
    assert _order(fresh) == epoch1 and epoch1 != epoch0 and sorted(epoch1) == DATA
    assert module_data.RFDETRDataModule.train_dataloader.__name__ == "train_dataloader"  # patch removed on exit


def test_sampler_without_generator_support_is_refused(fake_train_dataloader) -> None:
    fake_train_dataloader["sampler"] = "sequential"
    with ra._seeded_train_loader(torch.Generator(), {}), pytest.raises(CapabilityError) as err:
        module_data.RFDETRDataModule.train_dataloader(object())
    assert err.value.code == "SAMPLER_GENERATOR_UNSUPPORTED"


def test_loader_generator_state_is_a_required_state_key() -> None:
    assert "loader_generator_state" in ra.RfdetrAdapter("RFDETRNano").required_state_keys()
