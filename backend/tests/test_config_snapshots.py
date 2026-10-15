"""גרסאות הגדרות: שורה אחת לכל גרסה, עם תוכן הקבצים, ושחזור חישוב ישן."""
from __future__ import annotations

import json
import shutil

from sqlalchemy import func, select

from app.modules.conversation.models import CalculationRunRow, ConfigSnapshotRow
from app.modules.conversation.public import ConversationService, LLMChain, reproduce_run
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

NOA = ["מנהלת מוצר, שכר בסיס 18,500",
       "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים", "יש לי COROLLA CROSS", "300"]


def _count(sessions, model):
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_snapshot_per_config_version_and_reproduce(sessions, tmp_path):
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    rules = RulesStore(config)
    svc = ConversationService(sessions, rules, LLMChain())
    turn, token = svc.start()
    for m in NOA:
        turn = svc.send(turn.conversation_id, token, m)

    turn = svc.send(turn.conversation_id, token, "ATTO 3")
    noa_run = turn.results[0].run_id
    turn = svc.send(turn.conversation_id, token, "EV3")
    assert _count(sessions, CalculationRunRow) == 2
    assert _count(sessions, ConfigSnapshotRow) == 1          # אותה גרסה → אותה שורה

    with sessions() as s:
        snap = s.scalars(select(ConfigSnapshotRow)).one()
        assert snap.strauss_rules_json.encode("utf-8") == (CONFIG / "strauss-rules.json").read_bytes()
        assert snap.strauss_data_xlsx == (CONFIG / "strauss-data.xlsx").read_bytes()

    # שינוי בכללים: הבונוס החשמלי עולה → גרסה חדשה
    path = config / "strauss-rules.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = "test-changed"
    data["electric"]["netBonus"] = 400
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rules.reload()
    turn = svc.send(turn.conversation_id, token, "ATTO 3")
    assert turn.results[0].result["delta_display"] != "661"
    assert _count(sessions, ConfigSnapshotRow) == 2

    # השחזור משתמש בקבצים השמורים, לא בתיקייה ששונתה
    again = reproduce_run(sessions, noa_run)
    assert again.delta_display == 661 and again.rules_version == "mock-2026-10-12"
