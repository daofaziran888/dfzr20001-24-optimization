"""干燥工序领域服务：控制干燥温度曲线，监控含水率达标，校验上游发酵。

含水率补测条说明：
- 每一条补测条 = 某一炉（炉次）某一盘（盘号）的一次真实实测；
- 补测条只引用「当时所接的干燥确认记录」(`干燥记录ID`)，不占用干燥记录的
  `上游工序/上游记录ID` 字段，二者相互独立；
- 同一盘料允许多次实测，新的有效读数只作废「本盘」的上一条有效读数，
  同炉其它盘的补测条与领取单不受影响。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DryingService(BaseModuleService):
    MODULE = "drying"
    ENTITY = "干燥记录"
    OPERATION_TERM = "记录干燥"
    LIST_FIELDS = ["炉次", "起始温度", "终止温度", "干燥时长", "含水率", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["起始温度", "终止温度", "干燥时长", "含水率"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"记录干燥": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        start_temp = store.parse_weight(values.get("起始温度", "0"))
        end_temp = store.parse_weight(values.get("终止温度", "0"))
        if start_temp <= 0 or end_temp <= 0:
            errors.append("干燥温度必须大于0")
        if start_temp > end_temp:
            errors.append("起始温度不应高于终止温度")
        moisture = store.parse_weight(values.get("含水率", "0"))
        if moisture <= 0:
            errors.append("必须记录含水率")
        elif moisture > 7:
            pass  # 记录但放行，不同茶类要求不同
        # 上游工序校验
        upstream_module = values.get("上游工序")
        upstream_id = values.get("上游记录ID")
        if upstream_module and upstream_id is not None:
            upstream = store.find(upstream_module, int(upstream_id))
            if upstream is None:
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 不存在")
            elif upstream.get("status") != "已确认":
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 未确认，不能进行干燥")
        return errors

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        if action == "记录干燥":
            moisture = store.parse_weight(entry.get("含水率", "0"))
            if not self.is_moisture_standard(moisture):
                entry["moisture_warning"] = "含水率超标，建议复烘"
            upstream_weight = store.parse_weight(entry.get("上游重量", "0"))
            if upstream_weight > 0:
                entry["产出重量"] = round(upstream_weight * (1.0 - moisture / 100.0 * 0.5), 3)
        return True, None

    def is_moisture_standard(self, moisture: float, tea_type: str = "绿茶") -> bool:
        """检查含水率是否达标（绿茶标准≤7%，红茶≤6%，乌龙茶≤5%）。"""
        standards = {"绿茶": 7.0, "红茶": 6.0, "乌龙茶": 5.0}
        return moisture <= standards.get(tea_type, 7.0)

    def get_drying_by_fermentation(self, ferm_entry_id: int) -> list[dict[str, Any]]:
        """获取指定发酵记录之后的干燥记录。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows
                if r.get("上游记录ID") is not None
                and int(r.get("上游记录ID", -1)) == ferm_entry_id
                and r.get("上游工序") == "fermentation"]

    # ── 含水率补测条 ──

    def register_retest(
        self,
        drying_entry_id: int,
        values: dict[str, Any],
        *,
        request_id: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """在干燥工作台登记一次含水率补测（实测），返回补测条。

        补测条以「炉次 + 盘号」归属，只记录本次实测读数与被接入的干燥确认，
        不改写干燥记录本身，也不触碰其它盘的历史读数。
        """
        if request_id:
            cached = store.check_idempotent(f"retest:{request_id}")
            if cached:
                return cached.get("retest"), f"幂等：{cached.get('message', '补测条已登记')}"

        entry = store.find(self.MODULE, drying_entry_id)
        if entry is None:
            return None, f"干燥记录 {drying_entry_id} 不存在"
        if entry.get("frozen"):
            return None, f"干燥记录 {drying_entry_id} 已被冻结，不允许补测"
        if entry.get("status") == "已撤回":
            return None, "已撤回的干燥记录不允许补测"

        furnace = str(values.get("炉次") or entry.get("炉次") or "").strip()
        pan = str(values.get("盘号") or "").strip()
        if not furnace:
            return None, "补测必须指定炉次"
        if not pan:
            return None, "补测必须指定盘号（同一炉内按盘独立）"
        entry_furnace = str(entry.get("炉次") or "").strip()
        if entry_furnace and entry_furnace != furnace:
            return None, (f"干燥记录 {drying_entry_id} 属于炉次 {entry_furnace}，"
                          f"不能以炉次 {furnace} 补测")

        moisture = store.parse_weight(values.get("含水率", "0"))
        if moisture <= 0:
            return None, "补测必须记录实测含水率"
        if moisture > 100:
            return None, "含水率应在0-100之间"

        pan_rows = [r for r in store.moisture_retests()
                    if r.get("炉次") == furnace and r.get("盘号") == pan]
        previous_effective = next((r for r in pan_rows if r.get("有效")), None)

        retest_id = max((int(r.get("id", 0)) for r in store.moisture_retests()), default=0) + 1
        retest: dict[str, Any] = {
            "id": retest_id,
            "干燥记录ID": drying_entry_id,
            "干燥确认编号": entry.get("登记编号", ""),
            "批次号": values.get("批次号") or entry.get("批次号", ""),
            "炉次": furnace,
            "盘号": pan,
            "含水率": round(moisture, 2),
            "实测序号": len(pan_rows) + 1,
            "是否改正": previous_effective is not None,
            "状态": "已确认",
            "有效": True,
            "登记人": str(values.get("登记人") or entry.get("负责人") or "").strip(),
            "登记时间": time.time(),
            "备注": str(values.get("备注") or "").strip(),
            "来源": "干燥工作台补测",
            "替代了": previous_effective.get("id") if previous_effective else None,
            "被替代为": None,
        }

        # 只作废本盘的上一条有效读数；同炉其它盘原样保留。
        if previous_effective is not None:
            previous_effective["有效"] = False
            previous_effective["状态"] = "已作废"
            previous_effective["被替代为"] = retest_id
            store.log_event("drying", int(previous_effective["干燥记录ID"]),
                            "moisture_retest_superseded",
                            {"retest_id": previous_effective["id"],
                             "superseded_by": retest_id,
                             "炉次": furnace, "盘号": pan,
                             "含水率": previous_effective["含水率"]})

        store.moisture_retests().append(retest)
        # 炉次记录在干燥确认上，便于再次进入时回溯；盘号留在补测条上按盘区分。
        entry.setdefault("炉次", furnace)

        store.log_event("drying", drying_entry_id, "moisture_retest",
                        {"retest_id": retest_id, "炉次": furnace, "盘号": pan,
                         "含水率": retest["含水率"], "实测序号": retest["实测序号"],
                         "替代了": retest["替代了"],
                         "干燥确认编号": retest["干燥确认编号"]})

        message = (f"干燥记录 {drying_entry_id} 已接第 {retest['实测序号']} 次实测："
                   f"炉次 {furnace} 盘号 {pan} 含水率 {retest['含水率']}%")
        if previous_effective is not None:
            message += f"，已替代本盘上一条读数 {previous_effective['含水率']}%"

        # 通知分级：新读数是否让尚未正式办理的领取单需要重核。
        impact: dict[str, Any] = {"affected_pickups": [], "signed_pickups_kept": []}
        try:
            from app.services import service_for
            grading_svc = service_for("grading")
            if grading_svc is not None:
                impact = grading_svc.mark_pickups_needing_recheck(furnace, pan, retest)
        except Exception:
            # 分级联动失败不应回滚已经真实发生的实测记录。
            impact = {"affected_pickups": [], "signed_pickups_kept": [],
                      "warning": "分级联动暂不可用，补测条已登记"}
        retest["联动结果"] = impact

        if impact.get("affected_pickups"):
            message += f"；{len(impact['affected_pickups'])} 张未正式办理的领取单转为需重核"
        if impact.get("signed_pickups_kept"):
            message += f"；{len(impact['signed_pickups_kept'])} 张已签收领取单保持原含水结果不变"

        if request_id:
            store.record_idempotent(f"retest:{request_id}",
                                    {"retest": dict(retest), "message": message})
        return retest, message

    def list_retests(self, drying_entry_id: int) -> list[dict[str, Any]]:
        """列出某一干燥确认下所有盘的补测条，按盘号、实测序号排列。"""
        rows = [r for r in store.moisture_retests()
                if int(r.get("干燥记录ID", 0)) == drying_entry_id]
        return sorted(rows, key=lambda r: (str(r.get("盘号", "")), int(r.get("实测序号", 0))))

    def effective_retest(self, furnace: str, pan: str,
                         batch_no: str | None = None) -> dict[str, Any] | None:
        """取本盘当前唯一有效补测条（同炉其它盘不参与）。"""
        candidates = []
        for r in store.moisture_retests():
            if r.get("炉次") != furnace or r.get("盘号") != pan:
                continue
            if not r.get("有效") or r.get("状态") != "已确认":
                continue
            if batch_no and r.get("批次号") and r.get("批次号") != batch_no:
                continue
            candidates.append(r)
        if not candidates:
            return None
        return max(candidates, key=lambda r: int(r.get("实测序号", 0)))

    def retest_history(self, furnace: str, pan: str) -> list[dict[str, Any]]:
        """本盘全部实测历史（含已作废），用于查回原读数与改正。"""
        rows = [r for r in store.moisture_retests()
                if r.get("炉次") == furnace and r.get("盘号") == pan]
        return sorted(rows, key=lambda r: int(r.get("实测序号", 0)))

    def get_retests_by_pan(self, furnace: str, pan: str) -> list[dict[str, Any]]:
        """按炉次、盘号跨干燥确认查询补测条（含已作废的历次实测）。"""
        return self.retest_history(furnace, pan)
