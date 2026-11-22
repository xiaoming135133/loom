from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any


RELATIONS = ("挂载", "扩展", "新建", "待定")
AMBIGUITY_MARKERS = (
    "无法判断",
    "需要看上下文",
    "结合前文",
    "前一页",
    "没有章节名",
    "待定",
    "可能",
    "特殊情况",
)
EXTENSION_MARKERS = ("推论", "特例", "进一步", "例题", "证明", "补充")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
WORD = re.compile(r"[a-zA-Z]{2,}")
SYMBOL = re.compile(
    r"\\[a-zA-Z]+|[εδκαλμπθσφ∞∀∃≤≥≠≈∼∫∑√±]|"
    r"\b(?:lim|sin|cos|tan|log|exp|dx|dy|ds)\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").lower())


def tokens(text: str) -> list[str]:
    normalized = normalize(text)
    result: list[str] = []
    for run in CJK_RUN.findall(normalized):
        result.extend(run)
        result.extend(run[i : i + 2] for i in range(max(0, len(run) - 1)))
    result.extend(match.lower() for match in WORD.findall(normalized))
    result.extend(match.lower() for match in SYMBOL.findall(text or ""))
    return result


def cosine(left: str, right: str) -> float:
    a = Counter(tokens(left))
    b = Counter(tokens(right))
    if not a or not b:
        return 0.0
    dot = sum(a[key] * b.get(key, 0) for key in a)
    return dot / math.sqrt(sum(value * value for value in a.values()) * sum(value * value for value in b.values()))


def literal_similarity(query: str, target: str) -> float:
    q = normalize(query)
    t = normalize(target)
    if not q or not t:
        return 0.0
    ratio = SequenceMatcher(None, q, t).ratio()
    shorter, longer = sorted((q, t), key=len)
    contains = 1.0 if len(shorter) >= 3 and shorter in longer else 0.0
    q_tokens = set(tokens(q))
    t_tokens = set(tokens(t))
    overlap = len(q_tokens & t_tokens) / max(1, len(q_tokens))
    return min(1.0, max(ratio, contains * 0.88, overlap * 0.9))


def symbols(text: str) -> set[str]:
    found = {match.lower() for match in SYMBOL.findall(text or "")}
    if "ε" in found or "\\varepsilon" in found:
        found.update({"epsilon", "ε", "\\varepsilon"})
    if "δ" in found or "\\delta" in found:
        found.update({"delta", "δ", "\\delta"})
    return found


def symbol_similarity(left: str, right: str) -> tuple[float, list[str]]:
    a = symbols(left)
    b = symbols(right)
    common = sorted(a & b)
    if not a or not b:
        return 0.0, []
    return len(common) / len(a | b), common


def _node_text(node: dict[str, Any]) -> str:
    return " ".join(
        str(node.get(key) or "")
        for key in ("title", "summary", "content", "latex")
    )


def _score_node(query: str, node: dict[str, Any], path: str) -> dict[str, Any]:
    target = _node_text(node)
    semantic = min(1.0, cosine(query, target) * 2.35)
    literal = literal_similarity(query, f"{node.get('title', '')} {target}")
    symbol_score, common_symbols = symbol_similarity(query, target)
    symbol_score = min(1.0, symbol_score * 1.7)
    path_match = 1.0 if normalize(query) and normalize(query) in normalize(path) else 0.0
    score = 0.52 * semantic + 0.33 * literal + 0.15 * symbol_score
    if path_match:
        score = max(score, 0.36)
    title = normalize(str(node.get("title") or ""))
    if len(title) >= 3 and title in normalize(query):
        score = max(score, 0.72)
    return {
        "node": node,
        "score": min(0.98, score),
        "path": path,
        "routes": {
            "semantic": round(semantic, 4),
            "literal": round(literal, 4),
            "symbol": round(symbol_score, 4),
            "structure": round(path_match, 4),
        },
        "common_symbols": common_symbols,
    }


def build_paths(nodes: list[dict[str, Any]]) -> dict[str, str]:
    by_id = {node["id"]: node for node in nodes}
    paths: dict[str, str] = {}
    for node in nodes:
        parts: list[str] = []
        current: dict[str, Any] | None = node
        seen: set[str] = set()
        while current and current["id"] not in seen:
            seen.add(current["id"])
            parts.append(str(current["title"]))
            current = by_id.get(current.get("parent_id"))
        paths[node["id"]] = " / ".join(reversed(parts))
    return paths


def _route_candidates(
    query: str,
    nodes: list[dict[str, Any]],
    paths: dict[str, str],
) -> list[dict[str, Any]]:
    scored = [
        _score_node(query, node, paths.get(node["id"], node["title"]))
        for node in nodes
        if node["node_type"] != "course"
    ]
    routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    routes["semantic"] = sorted(scored, key=lambda item: item["routes"]["semantic"], reverse=True)[:10]
    routes["literal"] = sorted(scored, key=lambda item: item["routes"]["literal"], reverse=True)[:10]
    routes["symbol"] = sorted(scored, key=lambda item: item["routes"]["symbol"], reverse=True)[:10]

    union: dict[str, dict[str, Any]] = {}
    for route_name, route_items in routes.items():
        for rank, item in enumerate(route_items):
            node_id = item["node"]["id"]
            if node_id not in union:
                union[node_id] = {**item, "routes": dict(item["routes"]), "route_ranks": {}}
            union[node_id]["route_ranks"][route_name] = rank + 1

    candidates = []
    for item in union.values():
        if item["score"] < 0.055 and not item["route_ranks"]:
            continue
        reasons = [
            name
            for name in ("semantic", "literal", "symbol", "structure")
            if item["routes"][name] >= 0.12
        ]
        if item["common_symbols"]:
            reasons.append("符号：" + " ".join(item["common_symbols"][:4]))
        candidates.append(
            {
                "id": item["node"]["id"],
                "title": item["node"]["title"],
                "summary": item["node"].get("summary", ""),
                "path": item["path"],
                "score": round(item["score"], 3),
                "routes": {key: round(value, 3) for key, value in item["routes"].items()},
                "routeRanks": item["route_ranks"],
                "reasons": reasons,
            }
        )
    return sorted(candidates, key=lambda candidate: candidate["score"], reverse=True)[:7]


def judge_alignment(
    content: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    query = " ".join(
        str(content.get(key) or "")
        for key in ("title", "summary", "recognized_text", "latex")
    )
    paths = build_paths(nodes)
    candidates = _route_candidates(query, nodes, paths)
    best = candidates[0] if candidates else None
    best_score = float(best["score"]) if best else 0.0
    combined_text = f"{content.get('title', '')} {content.get('summary', '')}"
    ambiguous = any(marker in combined_text for marker in AMBIGUITY_MARKERS)
    extension = any(marker in combined_text for marker in EXTENSION_MARKERS)

    if ambiguous and best_score < 0.72:
        relation = "待定"
        target_id = None
        confidence = min(0.53, 0.36 + best_score * 0.22)
        reason = "当前内容缺少足以确认归属的上下文，先进入未归类箱，等同类内容积累后一起询问。"
    elif extension and best and best_score >= 0.18:
        relation = "扩展"
        target_id = best["id"]
        confidence = min(0.79, 0.57 + best_score * 0.26)
        reason = f"检测到「推论/特例」等细分信号，建议作为「{best['title']}」的子内容。"
    elif best and best_score >= 0.34:
        relation = "挂载"
        target_id = best["id"]
        confidence = min(0.95, 0.56 + best_score * 0.48)
        reason = f"内容与「{best['title']}」的定义和符号重合度最高，建议直接挂载。"
    elif ambiguous:
        relation = "待定"
        target_id = None
        confidence = 0.41
        reason = "上下文不足以判断归属，按产品规则不猜测。"
    else:
        relation = "新建"
        parent = best if best and best_score >= 0.12 else next(
            (node for node in nodes if node["node_type"] == "course"),
            None,
        )
        target_id = parent["id"] if parent else None
        confidence = min(0.88, 0.68 + best_score * 0.28)
        reason = (
            f"现有知识点中没有足够相近的容器，建议在「{parent['title']}」下新建一支。"
            if parent
            else "现有知识树中没有可容纳该内容的节点。"
        )

    if confidence >= 0.80:
        band = "auto"
    elif confidence >= 0.55:
        band = "review"
    else:
        band = "pending"

    return {
        "relation": relation,
        "targetId": target_id,
        "confidence": round(confidence, 3),
        "reason": reason,
        "thresholdBand": band,
        "candidates": candidates,
    }


def search_content(
    query: str,
    nodes: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    paths = build_paths(nodes)
    results: list[dict[str, Any]] = []
    for node in nodes:
        if node["node_type"] == "course":
            continue
        item = _score_node(query, node, paths.get(node["id"], node["title"]))
        if item["score"] < 0.07:
            continue
        reasons = []
        if item["routes"]["semantic"] >= 0.12:
            reasons.append("语义近邻")
        if item["routes"]["literal"] >= 0.12:
            reasons.append("字面命中")
        if item["routes"]["symbol"] >= 0.12:
            reasons.append("符号命中")
        if item["routes"]["structure"]:
            reasons.append("章节结构")
        if item["common_symbols"]:
            reasons.append("公式符号 " + " ".join(item["common_symbols"][:4]))
        associated = next((asset for asset in assets if asset.get("node_id") == node["id"]), None)
        results.append(
            {
                "type": "node",
                "id": node["id"],
                "title": node["title"],
                "snippet": node.get("summary", ""),
                "path": item["path"],
                "score": round(item["score"], 3),
                "reasons": reasons or ["章节结构"],
                "nodeId": node["id"],
                "assetId": associated["id"] if associated else None,
            }
        )

    for asset in assets:
        text = " ".join(
            str(asset.get(key) or "")
            for key in ("title", "summary", "recognized_text", "latex")
        )
        semantic = min(1.0, cosine(query, text) * 2.35)
        literal = literal_similarity(query, text)
        symbol_score, common_symbols = symbol_similarity(query, text)
        symbol_score = min(1.0, symbol_score * 1.7)
        score = 0.52 * semantic + 0.33 * literal + 0.15 * symbol_score
        if score < 0.09:
            continue
        reasons = []
        if semantic >= 0.12:
            reasons.append("语义近邻")
        if literal >= 0.12:
            reasons.append("原图内容命中")
        if symbol_score >= 0.12:
            reasons.append("符号命中")
        if common_symbols:
            reasons.append("公式符号 " + " ".join(common_symbols[:4]))
        results.append(
            {
                "type": "asset",
                "id": asset["id"],
                "title": asset["title"],
                "snippet": asset.get("summary", ""),
                "path": paths.get(asset.get("node_id") or "", "未归类箱"),
                "score": round(score, 3),
                "reasons": reasons or ["文本命中"],
                "nodeId": asset.get("node_id"),
                "assetId": asset["id"],
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]
