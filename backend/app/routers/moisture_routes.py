"""干燥补测条与精制领取单接口。

这些记录独立于通用 18 模块资源表：补测条按炉次/盘归属，领取单记录实际
领出的盘与使用的含水结果。路径使用独立前缀，避免与 /api/{module} 冲突。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import service_for

router = APIRouter(prefix="/api", tags=["干燥补测与精制领取"])


def _drying():
    svc = service_for("drying")
    if svc is None:
        raise HTTPException(status_code=500, detail="干燥服务不可用")
    return svc


def _grading():
    svc = service_for("grading")
    if svc is None:
        raise HTTPException(status_code=500, detail="分级服务不可用")
    return svc


# ── 含水率补测条 ──

@router.post("/moisture-retests")
def create_moisture_retest(payload: dict[str, Any]) -> dict[str, Any]:
    """干燥工作台提交一次含水率补测（一次真实实测）。"""
    drying_entry_id = payload.get("drying_entry_id") or payload.get("干燥记录ID")
    if drying_entry_id in (None, ""):
        raise HTTPException(status_code=400, detail="必须指定干燥记录ID")
    retest, message = _drying().register_retest(
        int(drying_entry_id), payload, request_id=str(payload.get("request_id") or ""))
    if retest is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "retest": retest}


@router.get("/moisture-retests")
def list_moisture_retests(
    drying_entry_id: int | None = Query(default=None),
    furnace: str | None = Query(default=None),
    pan: str | None = Query(default=None),
) -> dict[str, Any]:
    """按干燥确认或按炉次/盘号查询补测条（含已作废的历次实测）。"""
    svc = _drying()
    if drying_entry_id is not None:
        rows = svc.list_retests(drying_entry_id)
    elif furnace and pan:
        rows = svc.retest_history(furnace, pan)
    else:
        raise HTTPException(status_code=400, detail="请提供 drying_entry_id 或 furnace+pan")
    effective = svc.effective_retest(furnace, pan) if furnace and pan else None
    return {"ok": True, "items": rows, "effective": effective}


@router.get("/moisture-retests/pan-context")
def moisture_pan_context(
    furnace: str = Query(...),
    pan: str = Query(...),
    grading_entry_id: int | None = Query(default=None),
    batch_no: str | None = Query(default=None),
) -> dict[str, Any]:
    """重新进入分级台时，查本盘接过的干燥确认、历次实测与领取情况。"""
    return {"ok": True, "detail": _grading().pan_context(furnace, pan, grading_entry_id, batch_no)}


# ── 精制领取单 ──

@router.post("/grading-pickups")
def create_grading_pickup(payload: dict[str, Any]) -> dict[str, Any]:
    """从本盘领出精制料（只认本盘当前唯一有效补测条）。"""
    grading_entry_id = payload.get("grading_entry_id") or payload.get("分级记录ID")
    if grading_entry_id in (None, ""):
        raise HTTPException(status_code=400, detail="必须指定分级记录ID")
    pickup, message = _grading().start_pickup(
        int(grading_entry_id), payload, request_id=str(payload.get("request_id") or ""))
    if pickup is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "pickup": pickup}


@router.get("/grading-pickups")
def list_grading_pickups(
    furnace: str | None = Query(default=None),
    pan: str | None = Query(default=None),
    batch_no: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    items = _grading().list_pickups(furnace=furnace, pan=pan, batch_no=batch_no, status=status)
    return {"ok": True, "items": items}


@router.get("/grading-pickups/{pickup_id}")
def get_grading_pickup(pickup_id: int) -> dict[str, Any]:
    pickup = _grading().get_pickup(pickup_id)
    if pickup is None:
        raise HTTPException(status_code=404, detail=f"精制领取单 {pickup_id} 不存在")
    return {"ok": True, "pickup": pickup}


@router.post("/grading-pickups/{pickup_id}/sign")
def sign_grading_pickup(pickup_id: int) -> dict[str, Any]:
    pickup, message = _grading().sign_pickup(pickup_id)
    if pickup is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "pickup": pickup}


@router.post("/grading-pickups/{pickup_id}/resolve")
def resolve_grading_pickup(pickup_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    decision = str(payload.get("decision") or "")
    reason = str(payload.get("reason") or "")
    pickup, message = _grading().resolve_pickup_recheck(pickup_id, decision, reason=reason)
    if pickup is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "pickup": pickup}
