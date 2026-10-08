"""单一案例族、批次抽查和四组配对导出；规模目标不作硬性验收门槛。"""
import copy
import math
import random
import re
from collections import Counter, defaultdict
from decimal import Decimal

from .arithmetic import safe_calculate
from .core import OPS, approved, content_hash, norm, unique, validate_refs, validate_review
from .io import digest


def assign_cases(blueprints, cards, seed=42):
    unique(blueprints, "blueprint_id")
    groups = defaultdict(list)
    structures = {}
    for row in blueprints:
        if set(row) != {"blueprint_id", "case_family_id", "knowledge_ids", "operation", "structure", "split"}:
            raise ValueError("蓝图字段应为blueprint_id/case_family_id/knowledge_ids/operation/structure/split")
        if not row["structure"].strip() or not row["case_family_id"] or not row["knowledge_ids"]:
            raise ValueError("蓝图缺少结构、族或知识")
        if row["operation"] not in OPS:
            raise ValueError("蓝图操作无效")
        for kid in row["knowledge_ids"]:
            if kid not in cards or not approved(cards[kid]):
                raise ValueError("先由本人确认知识卡，再设计生成蓝图")
            if row["operation"] not in {o["operation"] for o in cards[kid]["supported_operations"]}:
                raise ValueError("蓝图超出知识允许操作")
        signature = norm(re.sub(r'\d+', 'N', row["structure"]))
        old = structures.setdefault(signature, row["case_family_id"])
        if old != row["case_family_id"]:
            raise ValueError("同构蓝图不得只改族ID，须合并case_family_id")
        groups[row["case_family_id"]].append(row)
    ids = sorted(groups)
    random.Random(seed).shuffle(ids)
    counts = Counter()
    result = []
    for fid in ids:
        splits = {r["split"] for r in groups[fid] if r["split"] is not None}
        if len(splits) > 1:
            raise ValueError("同一case_family_id不能跨集合")
        split = next(iter(splits)) if splits else max({"train": .7, "validation": .15, "test": .15}, key=lambda s: {"train": .7, "validation": .15, "test": .15}[s] * len(blueprints) - counts[s])
        if split not in {"train", "validation", "test", "diagnostic", "retention"}:
            raise ValueError("无效集合")
        result.extend({**r, "split": split} for r in groups[fid])
        counts[split] += len(groups[fid])
    return sorted(result, key=lambda r: r["blueprint_id"])


def answer_key(q):
    s = q["scoring_spec"]
    if s["answer_type"] == "numeric":
        number = Decimal(str(s["expected_value"]))
        unit = s["unit"]
        return ("numeric", number / 100 if unit == "%" else number, "ratio" if unit == "%" else unit)
    if s["answer_type"] in {"label", "insufficient"}:
        return (s["answer_type"], s["expected_value"])
    if s.get("comparison_key"):
        return ("text", s["comparison_key"])
    raise ValueError("文本三联关系需模型定义并由本人确认comparison_key")


def check_groups(data):
    families, groups = defaultdict(set), defaultdict(list)
    for q in data:
        families[q["case_family_id"]].add(q["split"])
        if q["group_id"]:
            groups[q["group_id"]].append(q)
        elif q["split"] in {"validation", "test"}:
            raise ValueError("评价题缺少group_id")
    if any(len(s) > 1 for s in families.values()):
        raise ValueError("case_family_id跨集合")
    for gid, rows in groups.items():
        variants = {q["variant_type"]: q for q in rows}
        if len(rows) != 3 or set(variants) != {"original", "irrelevant", "critical"}:
            raise ValueError(f"{gid}:三联题不完整")
        if len({q["split"] for q in rows}) != 1 or len({q["case_family_id"] for q in rows}) != 1:
            raise ValueError("group_id跨集合或案例族")
        keys = [answer_key(variants[t]) for t in ("original", "irrelevant", "critical")]
        def equal(a, b, left, right):
            if a[0] == b[0] == "numeric" and a[2] == b[2]:
                tol = max(Decimal(str(q["scoring_spec"]["tolerance"])) / (100 if q["scoring_spec"]["unit"] == "%" else 1) for q in (left, right))
                return abs(a[1] - b[1]) <= tol
            return a == b
        if not equal(keys[0], keys[1], variants["original"], variants["irrelevant"]) or equal(keys[0], keys[2], variants["original"], variants["critical"]):
            raise ValueError("无关变化应保持答案，关键变化应有意义地改变答案")


def check_questions(data, cards, sources, freeze):
    unique(data, "question_id")
    fragments = unique(sources, "fragment_id")
    blueprints = unique(freeze["case_blueprints"], "blueprint_id")
    for q in data:
        required = {"question_id", "revision", "knowledge_ids", "knowledge_revisions", "blueprint_id", "case_family_id", "group_id", "split", "operation", "pool", "batch_id", "variant_type", "context", "question", "canonical_reference", "plain_target", "semantic_target", "scoring_spec", "evidence_refs", "generation", "model_review", "human_review", "synthetic_demo"}
        if not required <= set(q):
            raise ValueError("题目字段不完整")
        if q["synthetic_demo"] or q["split"] not in {"train", "validation", "test", "diagnostic", "retention"}:
            raise ValueError("正式data不能混入演示数据或无效集合")
        b = blueprints.get(q["blueprint_id"])
        if b is None or any(q[k] != b[k] for k in ("case_family_id", "split", "operation")):
            raise ValueError("题目与预先分配蓝图不符")
        if not set(q["knowledge_ids"]) <= set(b["knowledge_ids"]):
            raise ValueError("题目超出蓝图知识范围")
        allowed = set()
        for kid in q["knowledge_ids"]:
            if kid not in cards or not approved(cards[kid]) or q["knowledge_revisions"].get(kid) != cards[kid]["revision"]:
                raise ValueError("知识未确认或版本过期，派生题需要复核")
            if q["operation"] not in {o["operation"] for o in cards[kid]["supported_operations"]}:
                raise ValueError("题目使用未获支持的操作")
            allowed.update(r["fragment_id"] for r in cards[kid]["evidence_refs"])
        validate_refs(q["evidence_refs"], fragments)
        if not {r["fragment_id"] for r in q["evidence_refs"]} <= allowed:
            raise ValueError("题目引用非知识卡依据")
        if q["operation"] == "combine" and len(set(q["knowledge_ids"])) < 2:
            raise ValueError("组合需要至少两张有联系的卡")
        if not q["plain_target"] or not q["semantic_target"] or not q["canonical_reference"]["final_answer"]:
            raise ValueError("缺少双目标或中立参考")
        spec = q["scoring_spec"]
        if spec["answer_type"] == "numeric":
            if spec["tolerance"] < 0 or not spec.get("expression"):
                raise ValueError("数值题缺少独立复算表达式或容差无效")
            if abs(safe_calculate(spec["expression"]) - Decimal(str(spec["expected_value"]))) > Decimal(str(spec["tolerance"])):
                raise ValueError("数值复算失败")
        if q["split"] == "train" and (q["pool"] not in {"ordinary", "coverage"} or q["group_id"] is not None):
            raise ValueError("训练问题池无效或混入评价组")
        validate_review(q)
    check_groups(data)


def duplicate_pairs(data):
    buckets = defaultdict(list)
    for q in data:
        # 不使用知识原文，避免把允许共享的知识视为泄漏。
        text = q["context"] + '\n' + q["question"]
        normalized = norm(text)
        for name in sorted(q.get("entity_names", []), key=len, reverse=True):
            text = text.replace(name, 'ENTITY')
        structural = re.sub(r'\d+(?:[.,]\d+)*', 'N', text)
        for method, signature in (("normalized", normalized), ("structural", norm(structural))):
            buckets[(method, signature)].append(q)
    pairs = {}
    import itertools
    for (method, _), rows in buckets.items():
        for a, b in itertools.combinations(rows, 2):
            if a["group_id"] and a["group_id"] == b["group_id"] and a["split"] == b["split"] and method == "structural":
                continue
            pair = sorted([a["question_id"], b["question_id"]])
            pid = digest(pair)
            item = pairs.setdefault(pid, {"pair_id": pid, "question_ids": pair, "methods": [], "cross_split": a["split"] != b["split"], "hashes": {q["question_id"]: content_hash(q) for q in (a, b)}})
            item["methods"].append(method)
    return list(pairs.values())


def sample_batch(data, cards, freeze, batch_id, seed=42):
    rows = [q for q in data if q["split"] == "train" and q["batch_id"] == batch_id]
    if not rows:
        raise ValueError("训练批次为空")
    if any(q["model_review"]["status"] != "passed" for q in rows):
        raise ValueError("先完成全量独立模型复核，再抽查")
    chosen = set()
    rng = random.Random(seed)
    for pool in {q["pool"] for q in rows}:
        previous = sum(info.get("pool_counts", {}).get(pool, 0) for bid, info in freeze["training_batches"].items() if bid != batch_id and info["status"] == "approved")
        local = sorted([q for q in rows if q["pool"] == pool], key=lambda q: q["question_id"])
        chosen.update(q["question_id"] for q in local[:max(0, 20 - previous)])
    for q in rows:
        if q.get("model_issues") or q["model_review"]["issues"] or any(cards[k]["formula_spec"] is not None or cards[k]["conditions"] or cards[k]["exceptions"] for k in q["knowledge_ids"]):
            chosen.add(q["question_id"])
    strata = defaultdict(list)
    for q in rows:
        if q["question_id"] not in chosen:
            strata[(q["pool"], q["operation"])].append(q["question_id"])
    for ids in strata.values():
        rng.shuffle(ids)
        chosen.update(ids[:max(1, math.ceil(len(ids) * .1))])
    remainder = sorted(q["question_id"] for q in rows if q["question_id"] not in chosen)
    rng.shuffle(remainder)
    chosen.update(remainder[:max(0, min(3, len(rows)) - len(chosen))])
    return {"status": "pending", "seed": seed, "sample_ids": sorted(chosen),
            "record_hashes": {q["question_id"]: content_hash(q) for q in rows},
            "pool_counts": dict(Counter(q["pool"] for q in rows)), "human_confirmation": None,
            "notes": "首批每池至少前20题全审；高风险/模型疑难全审；其余按池和操作约10%且整批至少3题。未抽中者不冒充逐题人工审核。"}


def training_ready(q, freeze):
    batch = freeze["training_batches"].get(q["batch_id"])
    if q["human_review"]["status"] in {"returned", "discarded"} or q["model_review"]["status"] != "passed":
        return False
    if not batch or batch["status"] != "approved" or batch["record_hashes"].get(q["question_id"]) != content_hash(q):
        return False
    if q["question_id"] in batch["sample_ids"] and q["human_review"]["status"] != "approved":
        return False
    return True


def export_groups(data, sources, freeze):
    fragments = unique(sources, "fragment_id")
    result = {g: [] for g in ("G1", "G2", "G3", "G4")}
    for q in sorted(data, key=lambda q: q["question_id"]):
        if q["split"] != "train":
            continue
        if not training_ready(q, freeze):
            raise ValueError("训练题未通过批次抽查，不能导出")
        text = '\n\n'.join(fragments[f]["clean_text"] for f in sorted({r["fragment_id"] for r in q["evidence_refs"]}))
        prompt = "相关知识\n" + text + "\n业务情境\n" + q["context"] + "\n问题\n" + q["question"] + "\n请依据给定资料给出结论及简短可核查的依据；资料不足时说明缺少什么。"
        groups = ("G1", "G3") if q["pool"] == "ordinary" else ("G2", "G4")
        for group, field in zip(groups, ("plain_target", "semantic_target")):
            result[group].append({"question_id": q["question_id"], "prompt": [{"role": "user", "content": prompt}], "completion": [{"role": "assistant", "content": q[field]}]})
    for a, b in (("G1", "G3"), ("G2", "G4")):
        if [(r["question_id"], r["prompt"]) for r in result[a]] != [(r["question_id"], r["prompt"]) for r in result[b]]:
            raise ValueError("配对输入不一致")
    return result
