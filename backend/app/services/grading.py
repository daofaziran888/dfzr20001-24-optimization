"""精制分级领域服务：按筛号规格分选、碎末比例控制、等级判定，校验上游干燥。

精制领取说明：
- 一次有效领取只针对「本盘」，必须引用本盘唯一有效的干燥补测条；
- 已签收的领取单是既成事实：补测再提交既不会在多领一次，也不会被撤掉；
- 未正式办理（等待入秤确认）的领取单在补测改数后转为「需重核」，
  由分级台看清是继续按原交接还是按新读数重核，再决定签收；
- 每张领取单都保留实际使用的补测条、含水结果与重试原因，可随时查回。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GradingService(BaseModuleService):
    MODULE = "grading"
    ENTITY = "分级结果"
    OPERATION_TERM = "确认分级"
    LIST_FIELDS = ["炉次", "盘号", "筛号规格", "碎末比例", "等级判定", "评审人员", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["筛号规格", "碎末比例", "等级判定", "评审人员"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认分级": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    VALID_GRADES = ["特级", "一级", "二级", "三级", "四级", "等外"]

    PICKUP_PENDING = "待入秤确认"
    PICKUP_SIGNED = "已签收"
    PICKUP_RECHECK = "需重核"

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        grade = values.get("等级判定", "")
        if grade not in self.VALID_GRADES:
            errors.append(f"等级判定须为{', '.join(self.VALID_GRADES)}之一")
        debris = store.parse_weight(values.get("碎末比例", "100"))
        if debris < 0 or debris > 100:
            errors.append("碎末比例格式无效")
        # 上游工序校验
        upstream_module = values.get("上游工序")
        upstream_id = values.get("上游记录ID")
        if upstream_module and upstream_id is not None:
            upstream = store.find(upstream_module, int(upstream_id))
            if upstream is None:
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 不存在")
            elif upstream.get("status") != "已确认":
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 未确认，不能进行分级")
        # 本盘补测条校验（若本次分级直接绑定了补测条）
        retest_id = values.get("补测条ID")
        if retest_id not in (None, ""):
            retest = store.find_moisture_retest(int(retest_id))
            if retest is None:
                errors.append(f"干燥补测条 {retest_id} 不存在")
            elif not retest.get("有效") or retest.get("状态") != "已确认":
                errors.append(f"干燥补测条 {retest_id} 已作废，不能用于本次分级")
        # 领取单校验
        pickup_id = values.get("领取单ID")
        if pickup_id not in (None, ""):
            pickup = store.find_grading_pickup(int(pickup_id))
            if pickup is None:
                errors.append(f"精制领取单 {pickup_id} 不存在")
            elif pickup.get("状态") != self.PICKUP_SIGNED:
                errors.append(f"领取单 {pickup_id} 状态为「{pickup.get('状态')}」，未入秤签收不能用于确认分级")
        return errors

    def auto_grade(self, debris_rate: float, sieve: str) -> str:
        """根据碎末率和筛号规格自动判定等级。"""
        if debris_rate <= 2:
            return "特级"
        elif debris_rate <= 5:
            return "一级"
        elif debris_rate <= 10:
            return "二级"
        elif debris_rate <= 20:
            return "三级"
        else:
            return "等外"

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """分级确认后更新批次产出重量。"""
        if action == "确认分级":
            pickup_id = entry.get("领取单ID")
            if pickup_id not in (None, ""):
                pickup = store.find_grading_pickup(int(pickup_id))
                if pickup is None:
                    return False, f"精制领取单 {pickup_id} 不存在，不能确认分级"
                if pickup.get("状态") != self.PICKUP_SIGNED:
                    return False, f"领取单 {pickup_id} 未入秤签收（当前「{pickup.get('状态')}」），请先完成签收或重核"
                # 把实际使用的含水结果固化到分级结果上，便于日后反查。
                entry["使用补测条ID"] = pickup.get("补测条ID")
                entry["使用含水率"] = pickup.get("含水率")
                entry["炉次"] = pickup.get("炉次")
                entry["盘号"] = pickup.get("盘号")
            debris = store.parse_weight(entry.get("碎末比例", "0"))
            input_weight = store.parse_weight(entry.get("上游重量", "0"))
            if input_weight > 0:
                entry["产出重量"] = round(input_weight * (1.0 - debris / 100.0), 3)
        return True, None

    def get_gradings_for_blending(self, batch_no: str) -> list[dict[str, Any]]:
        """获取指定批次可用于拼配的分级结果。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("批次号") == batch_no and r.get("status") == "已确认"]

    def get_by_grade(self, grade: str) -> list[dict[str, Any]]:
        """按等级获取分级结果。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("等级判定") == grade and r.get("status") == "已确认"]

    # ── 精制领取 ──

    def _drying_service(self):
        from app.services import service_for
        return service_for("drying")

    def _pickups_for(self, grading_entry_id: int, furnace: str, pan: str) -> list[dict[str, Any]]:
        return [
            p for p in store.grading_pickups()
            if int(p.get("分级记录ID", 0)) == int(grading_entry_id)
            and p.get("炉次") == furnace and p.get("盘号") == pan
        ]

    def start_pickup(
        self,
        grading_entry_id: int,
        values: dict[str, Any],
        *,
        request_id: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """为本盘领出一次精制料。

        必须且只能使用本盘当前唯一有效的干燥补测条。若本盘已有未签收的领取单，
        只把新读数接到这张单上（不重复领一次实物）；已签收的领取单保持原样。
        """
        if request_id:
            cached = store.check_idempotent(f"pickup:{request_id}")
            if cached:
                return cached.get("pickup"), f"幂等：{cached.get('message', '领取单已存在')}"

        grading = store.find(self.MODULE, grading_entry_id)
        if grading is None:
            return None, f"分级结果 {grading_entry_id} 不存在"
        if grading.get("frozen"):
            return None, f"分级结果 {grading_entry_id} 已被冻结，不允许领取"
        if grading.get("status") == "已确认":
            return None, "分级结果已确认，不再重新领料；如需返工请先登记新的分级记录"

        drying_svc = self._drying_service()
        if drying_svc is None:
            return None, "干燥服务不可用"

        furnace = str(values.get("炉次") or grading.get("炉次") or "").strip()
        pan = str(values.get("盘号") or grading.get("盘号") or "").strip()
        if not furnace or not pan:
            return None, "精制领取必须指定炉次与盘号（按盘领出）"

        requested_retest = values.get("补测条ID")
        effective = drying_svc.effective_retest(furnace, pan, grading.get("批次号"))
        if effective is None:
            return None, f"炉次 {furnace} 盘号 {pan} 尚无有效干燥补测条，不能领料"
        if requested_retest not in (None, ""):
            retest = store.find_moisture_retest(int(requested_retest))
            if retest is None:
                return None, f"干燥补测条 {requested_retest} 不存在"
            if retest.get("炉次") != furnace or retest.get("盘号") != pan:
                return None, (f"干燥补测条 {requested_retest} 属于炉次 {retest.get('炉次')}"
                              f"盘号 {retest.get('盘号')}，不能用于本盘 {furnace}/{pan}")
            if not retest.get("有效") or retest.get("状态") != "已确认":
                return None, f"干燥补测条 {requested_retest} 已作废，本盘当前有效读数为 {effective['id']}"
            if int(retest["id"]) != int(effective["id"]):
                return None, f"本盘当前有效补测条为 {effective['id']}，请按最新读数领料"

        existing = self._pickups_for(grading_entry_id, furnace, pan)

        signed = [p for p in existing if p.get("状态") == self.PICKUP_SIGNED]
        if signed:
            # 实物已经签收，绝不重复领用，也不动原有记录。
            return signed[-1], (f"炉次 {furnace} 盘号 {pan} 已于领取单 {signed[-1]['id']} "
                                f"签收，实际已领料不会重复")

        recheck = [p for p in existing if p.get("状态") == self.PICKUP_RECHECK]
        if recheck:
            # 补测已改数：保留「需重核」状态，重提确认只回读状态，
            # 由分级台明确选择继续按原交接还是按新读数重核。
            pickup = recheck[-1]
            return pickup, (f"领取单 {pickup['id']} 当前需重核（{pickup.get('重核原因')}）；"
                            f"请选择「继续按原交接」或「按新读数重核」，不会重复领料")

        reopen = [p for p in existing if p.get("状态") == self.PICKUP_PENDING]
        if reopen:
            pickup = reopen[-1]
            old_retest = pickup.get("补测条ID")
            pickup["补测条ID"] = effective["id"]
            pickup["含水率"] = effective["含水率"]
            pickup["干燥记录ID"] = effective.get("干燥记录ID")
            pickup["干燥确认编号"] = effective.get("干燥确认编号", "")
            pickup["状态"] = self.PICKUP_PENDING
            pickup["重核原因"] = ""
            if int(old_retest or 0) != int(effective["id"]):
                pickup.setdefault("更换读数记录", []).append({
                    "从": old_retest, "到": effective["id"], "时间": time.time(),
                })
            reason = str(values.get("重试原因") or "").strip()
            if reason and reason not in pickup.setdefault("重试原因", []):
                pickup["重试原因"].append(reason)
            store.log_event(self.MODULE, grading_entry_id, "pickup_relinked",
                            {"pickup_id": pickup["id"], "retest_id": effective["id"],
                             "furnace": furnace, "pan": pan})
            message = (f"沿用领取单 {pickup['id']}（炉次 {furnace} 盘号 {pan}），"
                       f"已接最新补测条 {effective['id']} 含水率 {effective['含水率']}%，未重复领实物")
            if request_id:
                store.record_idempotent(f"pickup:{request_id}",
                                        {"pickup": dict(pickup), "message": message})
            return pickup, message

        attempt = max((int(p.get("领取次数", 0)) for p in existing), default=0) + 1
        retry_reason = str(values.get("重试原因") or "").strip()
        if attempt > 1 and not retry_reason:
            return None, "本盘此前已领取过，重新领取必须说明重试原因"
        pickup_id = max((int(p.get("id", 0)) for p in store.grading_pickups()), default=0) + 1
        pickup: dict[str, Any] = {
            "id": pickup_id,
            "批次号": grading.get("批次号", ""),
            "分级记录ID": grading_entry_id,
            "炉次": furnace,
            "盘号": pan,
            "补测条ID": effective["id"],
            "含水率": effective["含水率"],
            "干燥记录ID": effective.get("干燥记录ID"),
            "干燥确认编号": effective.get("干燥确认编号", ""),
            "领出重量": str(values.get("领出重量") or grading.get("上游重量") or ""),
            "操作人": str(values.get("操作人") or grading.get("评审人员") or "").strip(),
            "状态": self.PICKUP_PENDING,
            "领取次数": attempt,
            "重试原因": [retry_reason] if retry_reason else [],
            "重核原因": "",
            "签收时间": None,
            "签收含水率": None,
            "领出时间": time.time(),
        }
        store.grading_pickups().append(pickup)

        # 领取单建立时，把实际来源回写到分级记录，刷新后仍能按原料来源重提确认。
        grading["领取单ID"] = pickup_id
        grading["炉次"] = furnace
        grading["盘号"] = pan
        grading["补测条ID"] = effective["id"]
        grading.setdefault("领取尝试", []).append({
            "领取单ID": pickup_id, "补测条ID": effective["id"],
            "含水率": effective["含水率"], "重试原因": retry_reason,
        })

        store.log_event(self.MODULE, grading_entry_id, "pickup",
                        {"pickup_id": pickup_id, "retest_id": effective["id"],
                         "furnace": furnace, "pan": pan,
                         "moisture": effective["含水率"], "attempt": attempt,
                         "retry_reason": retry_reason})
        message = (f"炉次 {furnace} 盘号 {pan} 已领出（领取单 {pickup_id}），"
                   f"使用补测条 {effective['id']} 含水率 {effective['含水率']}%")
        if request_id:
            store.record_idempotent(f"pickup:{request_id}",
                                    {"pickup": dict(pickup), "message": message})
        return pickup, message

    def sign_pickup(self, pickup_id: int) -> tuple[dict[str, Any] | None, str]:
        """入秤签收：确认实际拿到这一盘料。"""
        pickup = store.find_grading_pickup(pickup_id)
        if pickup is None:
            return None, f"精制领取单 {pickup_id} 不存在"
        if pickup.get("状态") == self.PICKUP_SIGNED:
            return pickup, f"领取单 {pickup_id} 已签收，无需重复签收"
        if pickup.get("状态") == self.PICKUP_RECHECK:
            return None, f"领取单 {pickup_id} 需重核，请先确认继续按原交接还是按新读数重核"
        if pickup.get("状态") != self.PICKUP_PENDING:
            return None, f"领取单 {pickup_id} 当前状态「{pickup.get('状态')}」不可签收"

        retest = store.find_moisture_retest(int(pickup.get("补测条ID", 0)))
        continuing = pickup.get("处理结论") == "继续按原交接"
        if retest is None or not retest.get("有效") or retest.get("状态") != "已确认":
            if not continuing:
                return None, (f"领取单 {pickup_id} 所引用的补测条 {pickup.get('补测条ID')} 已作废，"
                              "请按本盘最新读数重核后再签收")
            # 人工已明确「继续按原交接」：保留原读数作为交接依据，但如实标注其已作废。
            pickup["读数状态"] = "原读数已作废（继续按原交接）"
        else:
            pickup["读数状态"] = "当前有效读数"

        pickup["状态"] = self.PICKUP_SIGNED
        pickup["签收时间"] = time.time()
        pickup["签收含水率"] = pickup.get("含水率")
        pickup["实际使用补测条ID"] = pickup.get("补测条ID")
        pickup["实际使用含水率"] = pickup.get("含水率")
        store.log_event(self.MODULE, int(pickup.get("分级记录ID", 0)), "pickup_sign",
                        {"pickup_id": pickup_id, "retest_id": pickup.get("补测条ID"),
                         "moisture": pickup.get("含水率"),
                         "读数状态": pickup.get("读数状态")})
        return pickup, (f"领取单 {pickup_id} 已入秤签收，含水结果 {pickup.get('含水率')}%"
                        f"（补测条 {pickup.get('补测条ID')}，{pickup.get('读数状态')}）")

    def resolve_pickup_recheck(
        self,
        pickup_id: int,
        decision: str,
        *,
        reason: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """处理需重核的领取单：继续按原交接，或按最新读数重核。"""
        pickup = store.find_grading_pickup(pickup_id)
        if pickup is None:
            return None, f"精制领取单 {pickup_id} 不存在"
        if pickup.get("状态") != self.PICKUP_RECHECK:
            return None, f"领取单 {pickup_id} 当前状态「{pickup.get('状态')}」无需重核"

        drying_svc = self._drying_service()
        if decision in ("继续按原交接", "继续", "continue"):
            pickup["状态"] = self.PICKUP_PENDING
            pickup["重核原因"] = ""
            pickup["处理结论"] = "继续按原交接"
            if reason:
                pickup.setdefault("重试原因", []).append(reason)
            store.log_event(self.MODULE, int(pickup.get("分级记录ID", 0)),
                            "pickup_recheck_continue",
                            {"pickup_id": pickup_id, "retest_id": pickup.get("补测条ID")})
            return pickup, f"领取单 {pickup_id} 继续按原交接（含水 {pickup.get('含水率')}%），等待入秤确认"

        if decision in ("按新读数重核", "重核", "recheck"):
            effective = drying_svc.effective_retest(
                pickup.get("炉次", ""), pickup.get("盘号", ""), pickup.get("批次号")) \
                if drying_svc else None
            if effective is None:
                return None, f"炉次 {pickup.get('炉次')} 盘号 {pickup.get('盘号')} 尚无有效补测条，无法重核"
            old = pickup.get("补测条ID")
            pickup["补测条ID"] = effective["id"]
            pickup["含水率"] = effective["含水率"]
            pickup["干燥记录ID"] = effective.get("干燥记录ID")
            pickup["干燥确认编号"] = effective.get("干燥确认编号", "")
            pickup["状态"] = self.PICKUP_PENDING
            pickup["重核原因"] = ""
            pickup["处理结论"] = "按新读数重核"
            pickup.setdefault("更换读数记录", []).append({
                "从": old, "到": effective["id"], "原因": reason or "补测改数", "时间": time.time(),
            })
            if reason:
                pickup.setdefault("重试原因", []).append(reason)
            store.log_event(self.MODULE, int(pickup.get("分级记录ID", 0)),
                            "pickup_recheck_new_reading",
                            {"pickup_id": pickup_id, "from": old, "to": effective["id"]})
            return pickup, (f"领取单 {pickup_id} 已改按补测条 {effective['id']} "
                            f"含水 {effective['含水率']}% 重核，等待入秤确认")

        return None, "重核结论须为「继续按原交接」或「按新读数重核」"

    def mark_pickups_needing_recheck(
        self, furnace: str, pan: str, retest: dict[str, Any],
    ) -> dict[str, Any]:
        """补测改数后，把本盘尚未签收、且引用了旧读数的领取单转为需重核。

        已签收的领取单是既成事实，不新增、不撤销、不改写其含水结果。
        """
        affected = []
        signed_kept = []
        for pickup in store.grading_pickups():
            if pickup.get("炉次") != furnace or pickup.get("盘号") != pan:
                continue
            if pickup.get("状态") == self.PICKUP_SIGNED:
                signed_kept.append(pickup["id"])
                continue
            if pickup.get("状态") not in (self.PICKUP_PENDING, self.PICKUP_RECHECK):
                continue
            if int(pickup.get("补测条ID") or 0) == int(retest["id"]):
                continue
            pickup["状态"] = self.PICKUP_RECHECK
            pickup["重核原因"] = (
                f"干燥补报第 {retest.get('实测序号')} 次实测，含水率由 "
                f"{pickup.get('含水率')}% 改为 {retest.get('含水率')}%（补测条 {retest['id']}）"
            )
            pickup["待用补测条ID"] = retest["id"]
            pickup["待用含水率"] = retest["含水率"]
            affected.append(pickup["id"])
            store.log_event(self.MODULE, int(pickup.get("分级记录ID", 0)),
                            "pickup_needs_recheck",
                            {"pickup_id": pickup["id"], "retest_id": retest["id"],
                             "furnace": furnace, "pan": pan})
        return {"affected_pickups": affected, "signed_pickups_kept": signed_kept}

    # ── 查回 ──

    def get_pickup(self, pickup_id: int) -> dict[str, Any] | None:
        pickup = store.find_grading_pickup(pickup_id)
        if pickup is None:
            return None
        retest = store.find_moisture_retest(int(pickup.get("补测条ID") or 0))
        drying = store.find("drying", int(pickup.get("干燥记录ID") or 0))
        return {
            **pickup,
            "使用的补测条": retest,
            "补测条是否仍有效": bool(retest and retest.get("有效")),
            "接入的干燥确认": (
                {"id": drying.get("id"), "登记编号": drying.get("登记编号"),
                 "status": drying.get("status"), "炉次": drying.get("炉次")}
                if drying else None
            ),
            "重试原因": pickup.get("重试原因", []),
            "更换读数记录": pickup.get("更换读数记录", []),
        }

    def list_pickups(
        self, furnace: str | None = None, pan: str | None = None,
        batch_no: str | None = None, status: str | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for p in store.grading_pickups():
            if furnace and p.get("炉次") != furnace:
                continue
            if pan and p.get("盘号") != pan:
                continue
            if batch_no and p.get("批次号") != batch_no:
                continue
            if status and p.get("状态") != status:
                continue
            result.append(self.get_pickup(p["id"]))
        return sorted(result, key=lambda p: int(p.get("id", 0)))

    def pan_context(
        self, furnace: str, pan: str,
        grading_entry_id: int | None = None,
        batch_no: str | None = None,
    ) -> dict[str, Any]:
        """重新进入分级台时，查本盘接过的干燥确认、历次实测与领取情况。"""
        drying_svc = self._drying_service()
        retests = drying_svc.retest_history(furnace, pan) if drying_svc else []
        effective = drying_svc.effective_retest(furnace, pan, batch_no) if drying_svc else None

        drying_entries = []
        seen = set()
        for r in retests:
            did = int(r.get("干燥记录ID") or 0)
            if did in seen:
                continue
            seen.add(did)
            entry = store.find("drying", did)
            if entry:
                drying_entries.append({
                    "id": entry.get("id"),
                    "登记编号": entry.get("登记编号"),
                    "status": entry.get("status"),
                    "炉次": entry.get("炉次"),
                    "含水率": entry.get("含水率"),
                })

        pickups = self.list_pickups(furnace=furnace, pan=pan, batch_no=batch_no)
        unsettled = [p for p in pickups if p.get("状态") != self.PICKUP_SIGNED]

        # 新读数能否作用到本次新分级：只有本盘仍有未签收的领取单/新分级未确认时才需重核。
        new_reading_applies = bool(effective and grading_entry_id is not None
                                   and store.find(self.MODULE, grading_entry_id) is not None)
        target_grading = store.find(self.MODULE, grading_entry_id) if grading_entry_id else None
        if target_grading is not None and target_grading.get("status") == "已确认":
            new_reading_applies = False

        return {
            "炉次": furnace,
            "盘号": pan,
            "有效补测条": effective,
            "历次实测": retests,
            "接过的干燥确认": drying_entries,
            "领取单": pickups,
            "未正式办理": unsettled,
            "新读数能否作用于本次分级": new_reading_applies,
            "旧分级处置提示": (
                "旧分级尚未正式办理：请选择继续按原交接，或按最新读数重核后再签收"
                if unsettled else "本盘没有待处理的旧领取单"
            ),
        }
