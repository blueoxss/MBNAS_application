"""Static block design-origin catalog (DD-38): which model family a block comes from, what it does, where it is written.

The table is DATA.  Importing this module imports nothing heavy (no torch, ultralytics, rfdetr); the catalog that
``GET`` documents carry (and ``GET /structure/block-catalog`` returns) is made of the static fields only and
``source_path`` is ``None`` there.  ``source_path`` is resolved lazily, in-process cached, only when a structure
document is produced by an *explicit* check (``resolve_source=True``): the class is imported with ``importlib`` and
its file is found with ``inspect``; the result is the path relative to the interpreter's ``site-packages`` (``None``
for a class outside it, a class the installed version does not have, or an import failure).

Honesty rules (no guesses): a field is ``None`` unless this file's author could back it with the installed package's
docstring/cfg, a paper, or the ETRI source.  ``introduced_in`` is non-null only together with a ``reference``.  A module
that is not in the table gets ``origin_family: None`` ("unknown", shown as 출처 미상) - never a nearest-name guess.

Entry keys
  ``ENTRIES``         module name as written in a YAML row / reported by the parser (``nn.`` prefix is stripped).
  ``NATIVE_ENTRIES``  ``"<backend>:<ClassName>"`` for the native backends (``rfdetr``, ``etri_hybrid``), looked up
                      by the observed ``module_class``; a few generic containers are re-routed by ``native_path``
                      (``NATIVE_PATH_RULES``) where the ETRI source says what they are.

Entry fields: ``origin_family`` (str|None), ``introduced_in`` (str|None), ``role`` (str, Korean), ``reference``
(url or repo path | None), ``notes`` (str|None; "unknown" names what could not be established).
"""
from __future__ import annotations

import copy
import importlib
import inspect
import re
import site
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA = "mbnas-block-catalog/1"
ETRI_FAMILY = "ETRI mixed operator pool"
ETRI_HYBRID_FAMILY = "ETRI controlled hybrid"
TORCH_FAMILY = "PyTorch torch.nn"
ETRI_MIXED_SOURCE = ("mbnas_model_runtime/operators/etri_mixed_blocks.py "
                     "(vendored from yolo_ga_runtime/mixed_blocks.py sha256 4233dd32…)")
ETRI_HYBRID_SOURCE = ("mbnas_model_runtime/adapters/etri_hybrid/model.py "
                      "(verbatim from ETRI_NAS/notebooks/ETRI_NAS_CNN_Transformer_Experiment.ipynb, cell 10)")

_DOCS = "https://docs.ultralytics.com/reference/nn/modules/"
_TORCH = "https://pytorch.org/docs/stable/generated/torch.nn."
_ARXIV = "https://arxiv.org/abs/"
_RFDETR_REPO = "https://github.com/roboflow/rf-detr"

ENTRIES: dict[str, dict[str, Any]] = {}
NATIVE_ENTRIES: dict[str, dict[str, Any]] = {}
_IMPL: dict[str, str | None] = {}          # ENTRIES key -> "package.module:Attr" for source_path (None: nothing to resolve)
_NATIVE_IMPL: dict[str, str | None] = {}


def _add(table: dict, impl_table: dict, key: str, family: str | None, introduced: str | None, role: str,
         reference: str | None, notes: str | None = None, impl: str | None = None) -> None:
    if introduced is not None and not reference:
        raise ValueError(f"{key}: introduced_in needs a reference")
    if key in table:
        raise ValueError(f"duplicate catalog key {key}")
    table[key] = {"origin_family": family, "introduced_in": introduced, "role": role, "reference": reference,
                  "notes": notes}
    impl_table[key] = impl


def _ul(name: str, file: str, family: str | None, introduced: str | None, role: str, reference: str | None = "docs",
        notes: str | None = None) -> None:
    """Ultralytics module ``ultralytics.nn.modules.<file>.<name>``; ``reference='docs'`` -> the docs page of the class."""
    ref = f"{_DOCS}{file}/#ultralytics.nn.modules.{file}.{name}" if reference == "docs" else reference
    _add(ENTRIES, _IMPL, name, family, introduced, role, ref, notes, impl=f"ultralytics.nn.modules.{file}:{name}")


def _torch(name: str, role: str, notes: str | None = None) -> None:
    _add(ENTRIES, _IMPL, name, TORCH_FAMILY, None, role, f"{_TORCH}{name}.html", notes, impl=f"torch.nn:{name}")


def _native(key: str, family: str | None, introduced: str | None, role: str, reference: str | None,
            notes: str | None = None, impl: str | None = None) -> None:
    _add(NATIVE_ENTRIES, _NATIVE_IMPL, key, family, introduced, role, reference, notes, impl)


# -- Ultralytics: conv.py -------------------------------------------------------------------------------------------
_LW = _ARXIV + "2406.03459"
_E_MODEL = "mbnas_model_runtime.adapters.etri_hybrid.model"


def _build_tables() -> bool:
    """Fill ENTRIES/NATIVE_ENTRIES (static data). Wrapped in a function so that importing this module executes no
    top-level call statements (public import-safety rule); the content is byte-identical to the former module-level calls."""
    _ul("Conv", "conv", "YOLOv5", "YOLOv5", "표준 합성곱: Conv2d + BatchNorm + 활성화(기본 SiLU)")
    _ul("Conv2", "conv", None, None, "RepConv 단순화판: 3x3 합성곱과 1x1 합성곱을 병렬로 두고 추론 시 융합(Conv fusing)",
        notes="설치본 docstring 'Simplified RepConv module with Conv fusing'. 계열·도입 시점 unknown")
    _ul("DWConv", "conv", "YOLOv5", "YOLOv5", "깊이별(depth-wise) 합성곱: groups = gcd(c1, c2)")
    _ul("GhostConv", "conv", "GhostNet", "GhostNet (2020)", "Ghost 합성곱: 적은 수의 기본 특징맵에 저비용 depth-wise 연산을 더해 특징맵을 늘림",
        reference=_ARXIV + "1911.11907", notes="설치본 docstring이 huawei-noah/Efficient-AI-Backbones 구현을 참조")
    _ul("LightConv", "conv", "PP-HGNet", "PP-HGNetV2", "경량 합성곱: 1x1 합성곱 + depth-wise 합성곱",
        notes="설치본 docstring: PaddleDetection HGNetV2 backbone 기반")
    _ul("RepConv", "conv", "RepVGG", "RepVGG (2021)", "재매개변수화 합성곱: 학습 시 3x3 + 1x1 (+BN 항등) 분기, 배포 시 하나의 3x3으로 융합",
        reference=_ARXIV + "2101.03697", notes="설치본 docstring이 DingXiaoH/RepVGG 구현을 참조. rt-detr cfg(RepC3 내부)에서 쓰임")
    _ul("ConvTranspose", "conv", None, None, "전치 합성곱 + (BatchNorm) + 활성화",
        notes="설치본 docstring 'Convolution transpose module with optional batch normalization and activation'. 계열·도입 시점 unknown")
    _ul("Focus", "conv", "YOLOv5", "YOLOv5", "Focus: 2x2 공간 슬라이스를 채널로 쌓은 뒤 합성곱 (stem 용)")
    _ul("Concat", "conv", "YOLOv5", None, "지정 차원(기본 채널)으로 텐서를 이어 붙이는 병합",
        notes="ultralytics/yolov5의 models/common.py에도 같은 이름이 있음. 최초 도입 시점 unknown")
    _ul("Index", "conv", None, None, "입력 리스트에서 지정한 인덱스의 원소를 선택",
        notes="설치본 docstring 'Returns a particular index of the input'. 계열·도입 시점 unknown")
    _ul("DWConvTranspose2d", "conv", None, None, "깊이별(depth-wise) 전치 합성곱", notes="계열·도입 시점 unknown")

    # -- Ultralytics: block.py ------------------------------------------------------------------------------------------
    _ul("Bottleneck", "block", "YOLOv5", "YOLOv5", "표준 병목 잔차 블록 (1x1/3x3 합성곱 + shortcut)")
    _ul("BottleneckCSP", "block", "CSPNet", "CSPNet (2019)", "CSP 병목: 입력을 두 경로로 나눠 한쪽에 Bottleneck을 반복한 뒤 결합",
        reference=_ARXIV + "1911.11929", notes="설치본 docstring이 WongKinYiu/CrossStagePartialNetworks를 참조")
    _ul("C1", "block", None, None, "CSP 병목(합성곱 1개 변형)", notes="설치본 docstring 'CSP Bottleneck with 1 convolution'. 계열·도입 시점 unknown")
    _ul("C2", "block", None, None, "CSP 병목(합성곱 2개 변형)", notes="설치본 docstring 'CSP Bottleneck with 2 convolutions'. 계열·도입 시점 unknown")
    _ul("C2f", "block", "YOLOv8", "YOLOv8", "C2f: 분할한 특징에 병목을 반복 적용하고 모든 중간 출력을 이어 붙이는 빠른 CSP 블록")
    _ul("C3", "block", "YOLOv5", "YOLOv5", "C3: 합성곱 3개로 구성된 CSP 병목")
    _ul("C3x", "block", "YOLOv5", "YOLOv5", "C3 + 교차 합성곱(cross convolution) 병목")
    _ul("C3TR", "block", "YOLOv5", "YOLOv5", "C3의 병목을 TransformerBlock(Vision Transformer 블록)으로 교체한 모듈",
        notes="설치본 TransformerBlock docstring이 arXiv 2010.11929(ViT)를 참조")
    _ul("C3Ghost", "block", "YOLOv5", "YOLOv5", "C3의 병목을 GhostBottleneck으로 교체한 모듈")
    _ul("C3k2", "block", "YOLO11", "YOLO11", "C3k2: C2f 변형. 내부 블록이 Bottleneck, C3k, 또는 (attn 설정 시) Bottleneck + PSABlock")
    _ul("C3k", "block", "YOLO11", "YOLO11", "커널 크기를 지정할 수 있는 C3 (C3k2의 내부 블록)")
    _ul("C2PSA", "block", "YOLO11", "YOLO11", "C2 구조에 PSA(attention) 블록을 쌓은 모듈 (yolo11/yolo26 backbone 끝부분)")
    _ul("PSA", "block", "YOLOv10", "YOLOv10", "PSA(부분 self-attention): 채널 일부에만 attention + FFN 적용",
        reference=_ARXIV + "2405.14458", notes="설치본 docstring은 'Position-Sensitive Attention'으로 표기")
    _ul("C2fPSA", "block", None, None, "C2f 구조에 PSABlock을 쌓은 모듈", notes="설치본 docstring 'C2fPSA module with enhanced feature extraction using PSA blocks'. 계열·도입 시점 unknown")
    _ul("C2fAttn", "block", "YOLO-World", "YOLO-World", "C2f + 추가 attention(MaxSigmoidAttnBlock; 텍스트 임베딩 유도형)",
        reference=_ARXIV + "2401.17270", notes="설치본 cfg는 v8 디렉터리(yolov8-world)에서 사용")
    _ul("A2C2f", "block", "YOLOv12", "YOLOv12", "Area-Attention C2f: 특징맵을 영역으로 나눠 attention을 적용하는 C2f",
        reference=_ARXIV + "2502.12524")
    _ul("C2fCIB", "block", "YOLOv10", "YOLOv10", "C2f의 내부 블록을 CIB(Compact Inverted Block)로 교체한 모듈", reference=_ARXIV + "2405.14458")
    _ul("CIB", "block", "YOLOv10", "YOLOv10", "Compact Inverted Block (C2fCIB의 내부 블록)", reference=_ARXIV + "2405.14458")
    _ul("SCDown", "block", "YOLOv10", "YOLOv10", "공간-채널 분리 다운샘플링: 1x1 합성곱 후 depth-wise stride-2 합성곱", reference=_ARXIV + "2405.14458")
    _ul("SPP", "block", "SPPNet", "SPPNet (2014)", "Spatial Pyramid Pooling: 여러 크기의 max-pool 결과를 이어 붙임",
        reference=_ARXIV + "1406.4729", notes="설치본 cfg는 v3 디렉터리(YOLOv3-SPP 계열)에서 사용")
    _ul("SPPF", "block", "YOLOv5", "YOLOv5 (v6.0)", "SPP 고속판: 같은 max-pool을 직렬 3회 적용해 결과를 이어 붙임",
        notes="설치본 docstring 'Spatial Pyramid Pooling - Fast (SPPF) layer for YOLOv5 by Glenn Jocher'")
    _ul("SPPELAN", "block", "YOLOv9", "YOLOv9", "SPP + ELAN 결합 블록", reference=_ARXIV + "2402.13616")
    _ul("HGStem", "block", "PP-HGNet", "PP-HGNetV2", "PP-HGNetV2 stem: 합성곱 5개 + maxpool 1개",
        notes="설치본 docstring 'StemBlock of PPHGNetV2'. 설치본 cfg는 rt-detr 디렉터리에서 사용")
    _ul("HGBlock", "block", "PP-HGNet", "PP-HGNetV2", "PP-HGNetV2 HG_Block: 합성곱 + LightConv",
        notes="설치본 docstring 'HG_Block of PPHGNetV2 with 2 convolutions and LightConv'. 설치본 cfg는 rt-detr 디렉터리에서 사용")
    _ul("RepC3", "block", "RT-DETR", "RT-DETR", "RepConv를 쌓은 C3 형태 블록 (rt-detr cfg의 neck 융합에 사용)", reference=_ARXIV + "2304.08069")
    _ul("ELAN1", "block", "YOLOv9", "YOLOv9", "합성곱 4개로 구성된 ELAN 블록", reference=_ARXIV + "2402.13616")
    _ul("ADown", "block", "YOLOv9", "YOLOv9", "다운샘플링: 채널을 나눠 stride-2 합성곱 경로와 maxpool 경로를 결합", reference=_ARXIV + "2402.13616")
    _ul("AConv", "block", "YOLOv9", "YOLOv9", "다운샘플링: AvgPool 후 stride-2 합성곱", reference=_ARXIV + "2402.13616")
    _ul("RepNCSPELAN4", "block", "YOLOv9", "YOLOv9", "CSP-ELAN (RepNCSP 블록 기반 ELAN)", reference=_ARXIV + "2402.13616")
    _ul("CBLinear", "block", "YOLOv9", "YOLOv9", "1x1 합성곱 출력을 채널 단위로 분할하는 투영 (YOLOv9 cfg 사용)", reference=_ARXIV + "2402.13616",
        notes="설치본 docstring에 설명 없음; 상세 설계 의도 unknown")
    _ul("CBFuse", "block", "YOLOv9", "YOLOv9", "여러 입력을 보간해 합산하는 융합 (YOLOv9 cfg 사용)", reference=_ARXIV + "2402.13616",
        notes="설치본 docstring에 설명 없음; 상세 설계 의도 unknown")
    _ul("ImagePoolingAttn", "block", "YOLO-World", "YOLO-World", "텍스트 임베딩을 이미지 특징으로 보강하는 attention", reference=_ARXIV + "2401.17270")
    _ul("ResNetLayer", "block", "ResNet", "ResNet (2015)", "ResNet 레이어: 여러 ResNet 블록의 묶음", reference=_ARXIV + "1512.03385",
        notes="설치본 cfg는 rt-detr 디렉터리(rtdetr-resnet*)에서 사용")
    _ul("TorchVision", "block", "torchvision", None, "torchvision 모델을 임의로 불러 쓰는 래퍼", reference="https://pytorch.org/vision/stable/models.html",
        notes="설치본 cfg는 11 디렉터리의 분류 cfg에서 사용. 도입 시점 unknown")

    # -- Ultralytics: head.py / transformer.py --------------------------------------------------------------------------
    _ul("Detect", "head", "YOLOv8", "YOLOv8", "Detect head: anchor-free 분리형(box/class) 검출 헤드",
        notes="yolo11/yolo26 cfg도 같은 클래스를 사용한다. end2end 여부는 구조 문서의 effective.end2end를 본다")
    _ul("v10Detect", "head", "YOLOv10", "YOLOv10", "v10 검출 헤드 (이중 할당 학습, NMS-free 추론용)", reference="https://arxiv.org/abs/2405.14458")
    _ul("WorldDetect", "head", "YOLO-World", "YOLO-World", "텍스트 임베딩과 정렬되는 open-vocabulary 검출 헤드", reference=_ARXIV + "2401.17270")
    _ul("YOLOEDetect", "head", "YOLOE", "YOLOE", "텍스트/시각 프롬프트 임베딩 기반 검출 헤드", reference=_ARXIV + "2503.07465")
    _ul("RTDETRDecoder", "head", "RT-DETR", "RT-DETR", "Real-Time Deformable Transformer Decoder: query 선택 + deformable attention 디코더(검출 헤드)",
        reference=_ARXIV + "2304.08069")
    _ul("Segment", "head", "YOLOv8", "YOLOv8", "인스턴스 분할 헤드 (Detect + mask prototype)")
    _ul("Pose", "head", "YOLOv8", "YOLOv8", "keypoint(자세) 헤드")
    _ul("OBB", "head", "YOLOv8", None, "회전 박스(OBB) 검출 헤드", notes="OBB 태스크가 도입된 정확한 버전 unknown")
    _ul("Classify", "head", None, None, "분류 헤드", notes="설치본 docstring 'YOLO classification head'. 계열·도입 시점 unknown")
    _ul("Segment26", "head", "YOLO26", "YOLO26", "YOLO26 인스턴스 분할 헤드", notes="설치본 docstring 'YOLO26 Segment head'")
    _ul("Pose26", "head", "YOLO26", "YOLO26", "YOLO26 keypoint 헤드", notes="설치본 docstring 'YOLO26 Pose head'")
    _ul("OBB26", "head", "YOLO26", "YOLO26", "YOLO26 회전 박스 검출 헤드", notes="설치본 docstring 'YOLO26 OBB detection head'")
    _ul("YOLOESegment", "head", "YOLOE", "YOLOE", "텍스트 임베딩을 갖는 분할 헤드", reference=_ARXIV + "2503.07465")
    _ul("YOLOESegment26", "head", "YOLOE", None, "YOLOE 방식의 분할 헤드 (Proto26 마스크 생성)", reference=_ARXIV + "2503.07465",
        notes="설치본 docstring 'YOLOE-style segmentation head module using Proto26'. YOLO26 cfg에서 사용; 도입 버전 unknown")
    _ul("Depth", "head", None, None, "단안 깊이 추정 헤드", notes="설치본 cfg는 26 디렉터리에서만 사용. 계열·도입 시점 unknown")
    _ul("SemanticSegment", "head", None, None, "픽셀 단위 의미 분할 헤드", notes="설치본 cfg는 26 디렉터리에서만 사용. 계열·도입 시점 unknown")
    _ul("AIFI", "transformer", "RT-DETR", "RT-DETR", "AIFI(스케일 내 attention 기반 특징 상호작용): 최상위 스케일 특징에 transformer 인코더 레이어 + 2D 위치 임베딩",
        reference=_ARXIV + "2304.08069")

    # -- YOLOv9's original repository only (not in the installed ultralytics) ------------------------------------------------
    _add(ENTRIES, _IMPL, "Silence", "YOLOv9", "YOLOv9", "입력을 그대로 통과시키는 자리표시 레이어 (YOLOv9 원 저장소 cfg에서 보조 분기 입력에 사용)",
         "https://github.com/WongKinYiu/yolov9",
         "설치된 ultralytics에는 없다(설치본 v9 cfg는 nn.Identity를 사용) -> source_path는 항상 null", impl=None)

    # -- PyTorch layers used in YAML cfgs and native backends --------------------------------------------------------------
    _torch("Upsample", "특징맵 업샘플링 (nn.Upsample)")
    _torch("MaxPool2d", "2D max pooling")
    _torch("ConvTranspose2d", "2D 전치 합성곱")
    _torch("Identity", "입력을 그대로 반환하는 항등 레이어")
    _torch("ZeroPad2d", "0으로 테두리를 채우는 패딩")
    _torch("Sequential", "순차 컨테이너: 자식 모듈을 차례로 적용 (역할은 모델 안의 위치에 따라 다름)")
    _torch("ModuleList", "모듈 목록 컨테이너 (역할은 모델 안의 위치에 따라 다름)")
    _torch("Embedding", "임베딩 테이블 (역할은 모델 안의 위치에 따라 다름)")
    _torch("Linear", "완전연결(선형) 레이어 (역할은 모델 안의 위치에 따라 다름)")
    _torch("LayerNorm", "LayerNorm 정규화 레이어")
    _torch("TransformerEncoderLayer", "PyTorch Transformer 인코더 레이어 (self-attention + FFN)",
           notes="Vaswani et al. 2017 구조(arXiv 1706.03762)의 PyTorch 구현")
    _torch("TransformerEncoder", "PyTorch Transformer 인코더 레이어 스택")

    # -- ETRI mixed operator pool (YAML genes; registered into the ultralytics parser at run time) -------------------------
    _add(ENTRIES, _IMPL, "NASPatchMerge", ETRI_FAMILY, None,
         "patch merging(다운샘플): patch x patch 영역을 reshape로 이어 붙이고 Linear + LayerNorm으로 채널 투영 (합성곱 없음)",
         ETRI_MIXED_SOURCE,
         "Swin 스타일 patch merging과 비슷한 reshape + Linear 구조(정규화 위치는 Linear 뒤). mixed_blocks.py IMPLEMENTATION_VERSION=1.0.0 (현재 버전; 도입 시점 unknown). "
         "설치된 패키지가 아니므로 source_path는 null")
    _add(ENTRIES, _IMPL, "NASWindowTransformer", ETRI_FAMILY, None,
         "Swin 스타일 window attention: 이동(shift) 없는 지역 window self-attention + MLP, BCHW 유지",
         ETRI_MIXED_SOURCE,
         "소스 docstring: 'Custom window attention is not an implementation of a named published model'. 실제 heads = gcd(c2, 요청 heads). "
         "도입 시점 unknown. 설치된 패키지가 아니므로 source_path는 null")
    _add(ENTRIES, _IMPL, "NASGlobalTransformer", ETRI_FAMILY, None,
         "ViT 스타일 global attention: 전체 토큰 self-attention + MLP, 토큰 수 상한(max_tokens)을 명시적으로 검사",
         ETRI_MIXED_SOURCE,
         "고해상도 입력은 암묵적 pooling 없이 명시적으로 실패한다(소스 docstring). 도입 시점 unknown. 설치된 패키지가 아니므로 source_path는 null")

    # -- native backend: RF-DETR (rfdetr package) ---------------------------------------------------------------------------
    _native("rfdetr:DinoV2", "DINOv2", None, "RF-DETR 인코더 래퍼: DINOv2 ViT를 windowed attention 백본으로 사용 (rfdetr.models.backbone.dinov2)",
            _ARXIV + "2304.07193", "DINOv2 가중치 상태는 observation.dinov2_weights. 도입 시점 unknown", impl="rfdetr.models.backbone.dinov2:DinoV2")
    _native("rfdetr:WindowedDinov2WithRegistersBackbone", "DINOv2", None, "windowed attention을 넣은 Dinov2WithRegisters ViT 인코더 백본",
            _ARXIV + "2304.07193", "설치본 docstring 'Dinov2WithRegisters backbone, to be used with frameworks like DETR and MaskFormer'. 도입 시점 unknown",
            impl="rfdetr.models.backbone.dinov2_with_windowed_attn:WindowedDinov2WithRegistersBackbone")
    _native("rfdetr:MultiScaleProjector", "LW-DETR", "LW-DETR (2024)", "다중 스케일 projector: 인코더 특징에서 피라미드 특징을 구성",
            _LW, "설치본 docstring 'implements MultiScaleProjector in :paper:`lwdetr`'", impl="rfdetr.models.backbone.projector:MultiScaleProjector")
    _native("rfdetr:PositionEmbeddingSine", "DETR", "DETR (2020)", "사인/코사인 2D 위치 인코딩", _ARXIV + "2005.12872",
            "설치본 docstring: Attention is all you need의 위치 임베딩과 매우 비슷한 표준판", impl="rfdetr.models.position_encoding:PositionEmbeddingSine")
    _native("rfdetr:MLP", "RF-DETR", None, "작은 MLP(FFN): 박스 회귀 head, query 위치 head 등에 쓰이는 선형층 스택", _RFDETR_REPO,
            "rfdetr 패키지의 구현 위치 기준 분류. 원 설계 출처 unknown", impl="rfdetr.models.math:MLP")
    _native("rfdetr:TransformerDecoderLayer", "LW-DETR", None, "디코더 레이어: self-attention + 멀티스케일 deformable cross-attention + FFN (키포인트 서브네트워크 옵션)",
            _LW, "rfdetr 구현(rfdetr.models.transformer). 도입 시점 unknown", impl="rfdetr.models.transformer:TransformerDecoderLayer")
    _native("rfdetr:TransformerDecoder", "LW-DETR", None, "디코더 레이어 스택", _LW, "rfdetr 구현. 도입 시점 unknown",
            impl="rfdetr.models.transformer:TransformerDecoder")
    _native("rfdetr:Transformer", "LW-DETR", None, "인코더 출력 + query로 박스/클래스를 예측하는 transformer (two-stage query 선택 포함)", _LW,
            "rfdetr 구현. 도입 시점 unknown", impl="rfdetr.models.transformer:Transformer")
    _native("rfdetr:MSDeformAttn", "LW-DETR", None, "멀티스케일 deformable attention 연산", _LW,
            "연산 자체는 Deformable DETR(arXiv 2010.04159) 기원. 도입 시점 unknown", impl="rfdetr.models.ops.modules.ms_deform_attn:MSDeformAttn")
    _native("rfdetr:LWDETR", "LW-DETR", None, "RF-DETR 최상위 검출 모델 (Group DETR v3 module)", _LW,
            "설치본 docstring 'This is the Group DETR v3 module that performs object detection'. 도입 시점 unknown", impl="rfdetr.models.lwdetr:LWDETR")

    # -- native backend: ETRI controlled CNN-Transformer hybrid ---------------------------------------------------------------
    _native("etri_hybrid:CNNStem", ETRI_HYBRID_FAMILY, None, "CNN 스템: stride-2 합성곱 블록을 log2(patch)회 쌓아 patch 토큰을 만든다", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown", impl=f"{_E_MODEL}:CNNStem")
    _native("etri_hybrid:LinearPatchEmbedding", ETRI_HYBRID_FAMILY, None, "선형 patch 임베딩: patch를 펼쳐 Linear 투영 (transformer_only)", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown", impl=f"{_E_MODEL}:LinearPatchEmbedding")
    _native("etri_hybrid:CNNResidual", ETRI_HYBRID_FAMILY, None, "합성곱 잔차 블록 2개 합성곱 + shortcut (cnn_only 인코더)", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown", impl=f"{_E_MODEL}:CNNResidual")
    _native("etri_hybrid:TransformerEncoderLayer", ETRI_HYBRID_FAMILY, None, "pre-norm Transformer 인코더 레이어 (GELU, batch_first)", ETRI_HYBRID_SOURCE,
            "nn.TransformerEncoderLayer(Vaswani et al. 2017, arXiv 1706.03762)를 norm_first=True로 사용. 도입 시점 unknown", impl="torch.nn:TransformerEncoderLayer")
    _native("etri_hybrid:TransformerEncoder", ETRI_HYBRID_FAMILY, None, "Transformer 인코더 레이어 스택 (nn.TransformerEncoder)", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown", impl="torch.nn:TransformerEncoder")
    _native("etri_hybrid:head", ETRI_HYBRID_FAMILY, None, "예측 head: Linear-GELU-Linear 2층 (class_head, box_head)", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown. Sequential 컨테이너를 native_path로 식별", impl=None)
    _native("etri_hybrid:position", ETRI_HYBRID_FAMILY, None, "sin/cos 위치 표(등록 buffer): 스템 토큰에 더해진다", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown. buffer(Tensor)를 native_path로 식별", impl=None)
    _native("etri_hybrid:ControlledDetector", ETRI_HYBRID_FAMILY, None, "ETRI 통제 실험용 CNN-Transformer 검출 모델 전체", ETRI_HYBRID_SOURCE,
            "도입 시점 unknown", impl=f"{_E_MODEL}:ControlledDetector")
    return True


_CATALOG_BUILT = _build_tables()

# class re-routing by native_path where the class is a generic container/buffer: (backend, regex on native_path, classes, key)
NATIVE_PATH_RULES: tuple[tuple[str, str, frozenset[str], str], ...] = (
    ("etri_hybrid", r"^embedding(\.|$)", frozenset({"Sequential"}), "etri_hybrid:CNNStem"),
    ("etri_hybrid", r"^(class_head|box_head)$", frozenset({"Sequential"}), "etri_hybrid:head"),
    ("etri_hybrid", r"^position$", frozenset({"Tensor"}), "etri_hybrid:position"),
)
# native generic classes with no backend entry fall back to ENTRIES (torch layers); 'Tensor' has none (stays unknown)


# -- lookup ---------------------------------------------------------------------------------------------------------------
def normalize_name(name: Any) -> str | None:
    """YAML module name -> table key (``nn.Upsample`` -> ``Upsample``); non-strings -> None."""
    if not isinstance(name, str) or not name.strip():
        return None
    text = name.strip()
    for prefix in ("torch.nn.", "nn."):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text.split(".")[-1] if "." in text else text


def lookup(name: Any) -> tuple[str, dict[str, Any]] | None:
    key = normalize_name(name)
    if key is not None and key in ENTRIES:
        return key, ENTRIES[key]
    return None


def lookup_native(backend: str | None, module_class: Any, native_path: Any = None) -> tuple[str, dict[str, Any]] | None:
    """Entry for an observed native block: ``<backend>:<class>`` first, then a path rule for generic containers,
    then the generic torch layers (``ENTRIES``).  ``None`` when nothing is established (-> unknown)."""
    if isinstance(module_class, str) and module_class:
        key = f"{backend}:{module_class}"
        if key in NATIVE_ENTRIES:
            return key, NATIVE_ENTRIES[key]
        if isinstance(native_path, str):
            for rule_backend, pattern, classes, rule_key in NATIVE_PATH_RULES:
                if rule_backend == backend and module_class in classes and re.search(pattern, native_path):
                    return rule_key, NATIVE_ENTRIES[rule_key]
        if module_class in ENTRIES and ENTRIES[module_class]["origin_family"] == TORCH_FAMILY:
            return module_class, ENTRIES[module_class]
    return None


def static_table() -> dict[str, Any]:
    """The whole static table (what ``GET /structure/block-catalog`` returns).  No import happens here."""
    return {"schema": SCHEMA, "entries": copy.deepcopy(ENTRIES), "native_entries": copy.deepcopy(NATIVE_ENTRIES),
            "families": sorted({e["origin_family"] for e in (*ENTRIES.values(), *NATIVE_ENTRIES.values()) if e["origin_family"]})}


# -- source_path (lazy, cached) ---------------------------------------------------------------------------------------------
def _site_dirs() -> list[Path]:
    dirs: list[str] = []
    try:
        dirs.extend(site.getsitepackages())
    except Exception:  # noqa: BLE001 - some embedded interpreters have no getsitepackages
        pass
    try:
        dirs.append(site.getusersitepackages())
    except Exception:  # noqa: BLE001
        pass
    for scheme_key in ("purelib", "platlib"):
        try:
            dirs.append(sysconfig.get_paths()[scheme_key])
        except Exception:  # noqa: BLE001
            pass
    out: list[Path] = []
    for d in dirs:
        try:
            p = Path(d).resolve()
        except OSError:
            continue
        if p not in out:
            out.append(p)
    return out


@lru_cache(maxsize=None)
def resolve_source_path(impl: str | None) -> str | None:
    """``"pkg.module:Attr"`` -> file of the class relative to site-packages; ``None`` when it cannot be established.

    Imports the package (lazily).  Cached in-process, including a ``None`` outcome (a missing optional runtime stays
    ``None`` until the process restarts).
    """
    if not impl or ":" not in impl:
        return None
    module_name, attr = impl.split(":", 1)
    try:
        obj: Any = importlib.import_module(module_name)
        for part in attr.split("."):
            obj = getattr(obj, part)
        path = inspect.getsourcefile(obj) or inspect.getfile(obj)
    except Exception:  # noqa: BLE001 - ImportError, AttributeError, TypeError (builtin), OSError: all mean "not resolvable"
        return None
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    for base in _site_dirs():
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


# -- attaching to structure documents ---------------------------------------------------------------------------------------
UNKNOWN_NOTE = "unknown: 카탈로그에 등재되지 않은 모듈 (출처를 추정하지 않음)"


def _doc(key: str | None, entry: dict[str, Any] | None, impl: str | None, resolve_source: bool) -> dict[str, Any]:
    if entry is None:
        return {"catalog_key": None, "origin_family": None, "introduced_in": None, "role": None, "reference": None,
                "notes": UNKNOWN_NOTE, "source_path": None}
    return {"catalog_key": key, **entry, "source_path": resolve_source_path(impl) if resolve_source else None}


def catalog_for_yaml_block(module: Any, *, resolve_source: bool = False) -> dict[str, Any]:
    """``blocks[].catalog`` of a YAML-gene block (``module`` is the declared operator name)."""
    hit = lookup(module)
    return _doc(hit[0] if hit else None, hit[1] if hit else None, _IMPL.get(hit[0]) if hit else None, resolve_source)


def catalog_for_native_block(backend: str | None, module_class: Any, native_path: Any = None, *,
                             resolve_source: bool = False) -> dict[str, Any]:
    """``blocks[].catalog`` of a native-module block (``module_class`` is the observed ``type(m).__name__``)."""
    hit = lookup_native(backend, module_class, native_path)
    if hit is None:
        return _doc(None, None, None, resolve_source)
    key, entry = hit
    impl = _NATIVE_IMPL.get(key) if key in NATIVE_ENTRIES else _IMPL.get(key)
    return _doc(key, entry, impl, resolve_source)


def catalog_summary(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """``{by_origin_family: {family: count}, unknown: count}`` over blocks (a block without a known family is unknown)."""
    families: dict[str, int] = {}
    unknown = 0
    for block in blocks:
        cat = block.get("catalog") if isinstance(block, dict) else None
        family = cat.get("origin_family") if isinstance(cat, dict) else None
        if isinstance(family, str) and family:
            families[family] = families.get(family, 0) + 1
        else:
            unknown += 1
    return {"by_origin_family": dict(sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))), "unknown": unknown}


def attach_yaml_catalog(blocks: list[dict[str, Any]], *, resolve_source: bool = False) -> dict[str, Any]:
    for block in blocks:
        block["catalog"] = catalog_for_yaml_block(block.get("module") or block.get("name"), resolve_source=resolve_source)
    return catalog_summary(blocks)


def attach_native_catalog(blocks: list[dict[str, Any]], backend: str | None, *, resolve_source: bool = False) -> dict[str, Any]:
    for block in blocks:
        block["catalog"] = catalog_for_native_block(backend, block.get("module_class"), block.get("native_path"),
                                                    resolve_source=resolve_source)
    return catalog_summary(blocks)

