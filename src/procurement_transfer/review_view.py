"""只读浏览视图，从核心文件派生；不维护独立审核台账。"""
import html
import json
from pathlib import Path

from .io import write_text


def render(cards, sources, target):
    fragments = {r["fragment_id"]: r for r in sources}
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>知识卡审核</title>',
             '<style>body{font:16px/1.7 system-ui;margin:32px auto;max-width:1250px;padding:0 20px;color:#243044;background:#f4f6f8}article{background:white;padding:24px;margin:22px 0;border-radius:12px}h1,h2{color:#183951}.cols{display:grid;grid-template-columns:1fr 1fr;gap:28px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.65 system-ui}small{color:#64748b}.tag{background:#e8eef6;padding:4px 8px;border-radius:6px}@media(max-width:800px){.cols{grid-template-columns:1fr}}</style>',
             f'<h1>采购知识卡（共{len(cards)}张）</h1><p>模型复核与人工确认分开记录。此页只读，可随时由四份核心文件重建。未说明的条件、例外、单位显示为“未说明”。</p>']
    for i, c in enumerate(cards, 1):
        parts.append(f'<article id="{esc(c["knowledge_id"])}"><h2>{i:02d}. {esc(c["name"])}</h2><small>{esc(c["knowledge_id"])} · revision {c["revision"]} · {esc(c["type"])}</small><div class="cols"><section>')
        for label, field in (("知识内容", "content"), ("适用范围", "scope"), ("前提", "conditions"), ("例外", "exceptions"), ("公式口径", "formula_spec"), ("允许操作", "supported_operations"), ("生成记录", "generation"), ("模型复核", "model_review"), ("本人确认", "human_review")):
            value = c[field]
            text = "未说明" if value is None else (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
            parts.append(f'<strong>{label}</strong><pre>{esc(text)}</pre>')
        parts.append('</section><section><strong>原句依据与定位</strong>')
        for ref in c["evidence_refs"]:
            s = fragments[ref["fragment_id"]]
            parts.append(f'<p class="tag">{esc(s["title"])} · {esc(s["locator"])}</p><pre>{esc(ref["quote"])}</pre><small>支持字段：{esc(ref["fields"])}</small>')
            if s["issues"]:
                parts.append(f'<p>待核对/抽取边界：{esc(s["issues"])}</p>')
            parts.append(f'<details><summary>清洗后完整片段</summary><pre>{esc(s["clean_text"])}</pre></details>')
        parts.append('</section></div></article>')
    parts.append('</html>')
    write_text(Path(target), '\n'.join(parts), replace=True)
