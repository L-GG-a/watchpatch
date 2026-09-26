from difflib import unified_diff

from watchpatch.config import MAX_DIFF_LINES


def make_diff(old: str, new: str, old_label: str = "上次快照", new_label: str = "本次快照") -> str:
    lines = list(
        unified_diff(
            old.splitlines(), new.splitlines(), fromfile=old_label, tofile=new_label, lineterm=""
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + ["… 差异已截断，完整内容已保存在数据库。"]
    return "\n".join(lines)
