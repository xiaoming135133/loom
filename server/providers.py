from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

try:
    from .engine import judge_alignment
except ImportError:
    from engine import judge_alignment


@dataclass
class RecognitionResult:
    summary: str
    recognized_text: str
    latex: str
    regions: list[dict[str, Any]]
    status: str
    trace: dict[str, Any] = field(default_factory=dict)


class RecognitionProvider(Protocol):
    """真实 OCR / 公式服务实现此接口后，可注入 create_app。"""

    def recognize_sample(self, sample: dict[str, Any]) -> RecognitionResult: ...

    def recognize_upload(self, title: str, original_filename: str) -> RecognitionResult: ...


class RelationJudge(Protocol):
    """真实关系推理服务实现此接口，返回四选一结构与候选。"""

    def judge(self, content: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]: ...


class OfflineRecognitionProvider:
    """当前 MVP：样张读取固定分区，上传明确降级为人工确认。"""

    def recognize_sample(self, sample: dict[str, Any]) -> RecognitionResult:
        return RecognitionResult(
            summary=sample["summary"],
            recognized_text=sample["text"],
            latex=sample["latex"],
            regions=sample["regions"],
            status="processing",
            trace={
                "provider": "offline-sample-fixture",
                "mode": "demo",
                "recognitionMs": 32,
                "alignmentMs": None,
                "source": sample["id"],
            },
        )

    def recognize_upload(self, title: str, original_filename: str) -> RecognitionResult:
        return RecognitionResult(
            summary="",
            recognized_text="",
            latex="",
            regions=[],
            status="manual_required",
            trace={
                "provider": "offline",
                "mode": "manual-required",
                "recognitionMs": 0,
                "alignmentMs": None,
                "message": "未配置真实 OCR，已保留原图并等待人工确认文字。",
            },
        )


class OfflineRelationJudge:
    """当前 MVP：可解释的确定性排序与关系规则。"""

    def judge(self, content: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return judge_alignment(content, nodes)
