"""含水率补测 / 精制领取一致性测试。

覆盖需求里的关键场景：
1. 补测条独立于干燥记录的上游工序引用；
2. 补录某盘不覆盖同炉另一盘；同盘两次实测只作废本盘上一条；
3. 已签收的领取单不会被多加或撤掉；
4. 未入秤确认时补报前次含水率偏高 → 需重核；
5. 查回实际使用的含水结果、领料与重试原因；
6. 重新进入分级台能查回接过的干燥确认，新读数作用于新分级；
7. 补报后不重复领实物。
"""
from __future__ import annotations

import unittest

from app.services import service_for
from app.store import store


class MoistureRetestBase(unittest.TestCase):
    def setUp(self):
        store._moisture_retests.clear()
        store._grading_pickups.clear()
        self.drying = service_for("drying")
        self.grading = service_for("grading")

        # 一炉两盘：一条已确认干燥记录
        drying_row, errors = self.drying.create_entry({
            "炉次": "L-T1",
            "起始温度": "80",
            "终止温度": "95",
            "干燥时长": "4",
            "含水率": "9.0",
            "登记编号": "DRYI-T1",
            "负责人": "干燥工甲",
            "批次号": "BATCH-T1",
        })
        self.assertIsNotNone(drying_row, errors)
        self.drying.run_action(int(drying_row["id"]), "记录干燥")
        self.drying_row = drying_row

        # 一条尚未确认的分级记录
        grading_row, errors = self.grading.create_entry({
            "筛号规格": "20目",
            "碎末比例": "3",
            "等级判定": "一级",
            "评审人员": "评审员甲",
            "登记编号": "GRAD-T1",
            "批次号": "BATCH-T1",
            "炉次": "L-T1",
            "盘号": "3",
        })
        self.assertIsNotNone(grading_row, errors)
        self.grading_row = grading_row

    def retest(self, pan: str, moisture: str, **extra):
        values = {"炉次": "L-T1", "盘号": pan, "含水率": moisture, "登记人": "干燥工甲"}
        values.update(extra)
        return self.drying.register_retest(int(self.drying_row["id"]), values)


class TestRetestIndependence(MoistureRetestBase):
    def test_retest_does_not_touch_upstream_reference(self):
        """补测条独立于干燥记录的上游工序引用。"""
        before = dict(self.drying_row)
        retest, msg = self.retest("3", "6.5")
        self.assertIsNotNone(retest, msg)
        self.assertEqual(self.drying_row.get("上游工序"), before.get("上游工序"))
        self.assertEqual(self.drying_row.get("上游记录ID"), before.get("上游记录ID"))
        self.assertEqual(retest["干燥记录ID"], self.drying_row["id"])

    def test_other_pan_not_overwritten(self):
        """补录 3 号盘不能覆盖同炉 5 号盘的有效读数。"""
        r5, _ = self.retest("5", "6.0")
        r3, _ = self.retest("3", "6.8")
        self.assertTrue(r5["有效"])
        self.assertTrue(r3["有效"])
        eff5 = self.drying.effective_retest("L-T1", "5")
        self.assertEqual(eff5["id"], r5["id"])

    def test_same_pan_two_readings_only_voids_previous(self):
        """同一盘两次实测：只作废本盘上一条，历次仍可查回。"""
        first, _ = self.retest("3", "8.0")
        second, msg = self.retest("3", "6.4")
        self.assertIsNotNone(second, msg)
        self.assertFalse(first["有效"])
        self.assertEqual(first["状态"], "已作废")
        self.assertEqual(first["被替代为"], second["id"])
        self.assertEqual(second["替代了"], first["id"])
        self.assertEqual(second["实测序号"], 2)
        history = self.drying.retest_history("L-T1", "3")
        self.assertEqual([r["id"] for r in history], [first["id"], second["id"]])
        self.assertEqual(self.drying.effective_retest("L-T1", "3")["id"], second["id"])


class TestGradingPickupConsistency(MoistureRetestBase):
    def _pickup(self, **extra):
        values = {"炉次": "L-T1", "盘号": "3"}
        values.update(extra)
        return self.grading.start_pickup(int(self.grading_row["id"]), values)

    def test_pickup_requires_own_pan_effective_retest(self):
        """没有本盘有效补测条不能领取；同炉其它盘读数不能借用。"""
        self.retest("5", "6.0")
        pickup, msg = self._pickup()
        self.assertIsNone(pickup)
        self.assertIn("尚无有效干燥补测条", msg)

        retest3, _ = self.retest("3", "6.6")
        pickup, msg = self._pickup(补测条ID=999999)
        self.assertIsNone(pickup)
        pickup, msg = self._pickup(补测条ID=retest3["id"])
        self.assertIsNotNone(pickup, msg)
        self.assertEqual(pickup["补测条ID"], retest3["id"])

    def test_signed_pickup_not_duplicated_or_removed(self):
        """已签收后补报：不重新领实物，也不撤掉已签收记录。"""
        self.retest("3", "6.6")
        pickup, _ = self._pickup()
        self.assertIsNotNone(pickup)
        signed, msg = self.grading.sign_pickup(int(pickup["id"]))
        self.assertIsNotNone(signed, msg)
        self.assertEqual(signed["状态"], "已签收")
        self.assertEqual(signed["签收含水率"], 6.6)

        again, msg = self._pickup(补测条ID=None)
        self.assertEqual(again["id"], pickup["id"])
        self.assertEqual(again["状态"], "已签收")
        self.assertIn("不会重复", msg)
        self.assertEqual(len(store.grading_pickups()), 1)

        # 新的补测条也不能撕掉已签收的实物
        self.retest("3", "7.2")
        self.assertEqual(store.find_grading_pickup(int(pickup["id"]))["状态"], "已签收")

    def test_pending_pickup_turns_needs_recheck_then_resolve(self):
        """未入秤确认时补报前次偏高 → 需重核；可选继续或按新读数。"""
        old_retest, _ = self.retest("3", "9.0")
        pickup, _ = self._pickup()
        self.assertEqual(pickup["状态"], "待入秤确认")

        new_retest, msg = self.retest("3", "6.2", 备注="前次偏高，补报")
        self.assertIn("需重核", msg)
        self.assertEqual(store.find_grading_pickup(int(pickup["id"]))["状态"], "需重核")
        self.assertIn("9.0", pickup["重核原因"])
        self.assertEqual(pickup["待用补测条ID"], new_retest["id"])

        # 需重核期间不能直接签收
        blocked, msg = self.grading.sign_pickup(int(pickup["id"]))
        self.assertIsNone(blocked)
        self.assertIn("需重核", msg)

        # 重新提交补测不会重新领料
        again, msg = self._pickup()
        self.assertEqual(again["id"], pickup["id"])
        self.assertIn("需重核", msg)
        self.assertEqual(len(store.grading_pickups()), 1)

        resolved, msg = self.grading.resolve_pickup_recheck(
            int(pickup["id"]), "按新读数重核")
        self.assertIsNotNone(resolved, msg)
        self.assertEqual(resolved["补测条ID"], new_retest["id"])
        self.assertEqual(resolved["含水率"], 6.2)
        self.assertEqual(resolved["状态"], "待入秤确认")

        signed, _ = self.grading.sign_pickup(int(pickup["id"]))
        self.assertEqual(signed["签收含水率"], 6.2)
        # 查回实际使用的含水结果与更换记录
        detail = self.grading.get_pickup(int(pickup["id"]))
        self.assertEqual(detail["使用的补测条"]["id"], new_retest["id"])
        self.assertTrue(detail["补测条是否仍有效"])
        self.assertEqual(detail["更换读数记录"][0]["从"], old_retest["id"])
        self.assertEqual(detail["实际使用含水率"], 6.2)

    def test_continue_original_handoff_keeps_old_reading(self):
        """旧分级未正式办理时，可继续按原交接。"""
        old, _ = self.retest("3", "9.0")
        pickup, _ = self._pickup()
        self.retest("3", "6.2")
        resolved, msg = self.grading.resolve_pickup_recheck(
            int(pickup["id"]), "继续按原交接")
        self.assertIsNotNone(resolved, msg)
        self.assertEqual(resolved["补测条ID"], old["id"])
        self.assertEqual(resolved["处理结论"], "继续按原交接")
        signed, msg = self.grading.sign_pickup(int(pickup["id"]))
        self.assertIsNotNone(signed, msg)
        self.assertEqual(signed["签收含水率"], 9.0)
        self.assertIn("原读数已作废", signed["读数状态"])
        detail = self.grading.get_pickup(int(pickup["id"]))
        self.assertEqual(detail["实际使用补测条ID"], old["id"])

    def test_retry_reason_recoverable(self):
        """重新领取必须说明重试原因，并可查回。"""
        self.retest("3", "7.5")
        pickup, _ = self._pickup()
        # 撤回未签收的领取单以模拟重新办理
        pickup["状态"] = "已作废"
        again, msg = self._pickup()
        self.assertIsNone(again)
        self.assertIn("重试原因", msg)
        again, msg = self._pickup(重试原因="秤台未收货，重新领出")
        self.assertIsNotNone(again, msg)
        self.assertIn("秤台未收货，重新领出", again["重试原因"])
        detail = self.grading.get_pickup(int(again["id"]))
        self.assertIn("秤台未收货，重新领出", detail["重试原因"])


class TestReEnterGrading(MoistureRetestBase):
    def test_pan_context_shows_drying_and_new_reading_applies(self):
        """重新进入分级台：能查当时接的是哪次干燥确认，新读数作用于本次新分级。"""
        self.retest("3", "9.0")
        pickup, _ = self.grading.start_pickup(
            int(self.grading_row["id"]), {"炉次": "L-T1", "盘号": "3"})
        self.assertIsNotNone(pickup)
        self.assertEqual(self.grading_row["领取单ID"], pickup["id"])

        new_retest, _ = self.retest("3", "6.3")

        # 模拟关闭并刷新分级页：从原料实际来源（分级记录上的炉次/盘号）重提确认
        fresh = store.find("grading", int(self.grading_row["id"]))
        ctx = self.grading.pan_context(
            fresh["炉次"], fresh["盘号"], int(fresh["id"]), fresh["批次号"])
        self.assertEqual(ctx["有效补测条"]["id"], new_retest["id"])
        self.assertEqual(len(ctx["接过的干燥确认"]), 1)
        self.assertEqual(ctx["接过的干燥确认"][0]["id"], self.drying_row["id"])
        self.assertEqual(len(ctx["历次实测"]), 2)
        self.assertTrue(ctx["新读数能否作用于本次分级"])
        self.assertTrue(ctx["未正式办理"])
        self.assertIn("继续按原交接", ctx["旧分级处置提示"])

    def test_new_reading_not_applied_to_confirmed_grading(self):
        """分级已确认后，新读数不再作用到旧分级。"""
        self.retest("3", "6.0")
        pickup, _ = self.grading.start_pickup(
            int(self.grading_row["id"]), {"炉次": "L-T1", "盘号": "3"})
        self.grading.sign_pickup(int(pickup["id"]))
        self.grading.run_action(int(self.grading_row["id"]), "确认分级")
        ctx = self.grading.pan_context(
            "L-T1", "3", int(self.grading_row["id"]), "BATCH-T1")
        self.assertFalse(ctx["新读数能否作用于本次分级"])

    def test_confirm_grading_uses_signed_pickup_reading(self):
        """确认分级时固化实际使用的补测条与含水结果。"""
        retest, _ = self.retest("3", "6.1")
        pickup, _ = self.grading.start_pickup(
            int(self.grading_row["id"]), {"炉次": "L-T1", "盘号": "3"})
        self.grading.sign_pickup(int(pickup["id"]))
        entry, msg = self.grading.run_action(int(self.grading_row["id"]), "确认分级")
        self.assertIsNotNone(entry, msg)
        self.assertEqual(entry["使用补测条ID"], retest["id"])
        self.assertEqual(entry["使用含水率"], 6.1)

    def test_confirm_grading_blocked_when_pickup_needs_recheck(self):
        """领取单需重核时，分级不能直接确认。"""
        self.retest("3", "9.0")
        pickup, _ = self.grading.start_pickup(
            int(self.grading_row["id"]), {"炉次": "L-T1", "盘号": "3"})
        self.retest("3", "6.2")
        entry, msg = self.grading.run_action(int(self.grading_row["id"]), "确认分级")
        self.assertIsNone(entry)
        self.assertIn("重核", msg)


if __name__ == "__main__":
    unittest.main()
